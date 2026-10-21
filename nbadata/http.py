"""The single choke point for every request to NBA.com.

Every call goes through NBAClient, which:
  1. checks the local cache first (a cached response is never re-downloaded),
  2. rate-limits live requests (minimum gap + random jitter + periodic long pause),
  3. retries transient failures with exponential backoff,
  4. stops the whole run if NBA.com looks like it's blocking us,
  5. saves the response text exactly as received (gzipped) -- this IS the raw layer.

Cache layout:
  data/cache/<endpoint>/<key>.json.gz    the response body, byte-for-byte as NBA.com sent it
  data/cache/<endpoint>/<key>.meta.json  what was asked for: endpoint, params, url, fetched_at
"""

import gzip
import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from nba_api.stats.library.http import STATS_HEADERS, NBAStatsResponse

log = logging.getLogger(__name__)

BASE_URL = "https://stats.nba.com/stats/{endpoint}"

# Worth retrying: rate limited, or NBA.com having a bad moment.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """A single request failed after all retries (or failed in a non-retryable way)."""


class BlockedError(Exception):
    """Several requests in a row failed. Probably rate-limited/blocked: stop and wait."""


def cache_key(endpoint: str, params: dict) -> str:
    """Stable id for a request. Same endpoint + same params -> same key, regardless of order."""
    canonical = json.dumps(
        {"endpoint": endpoint.lower(), "params": {k: "" if v is None else str(v) for k, v in params.items()}},
        sort_keys=True,
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:20]


class NBAClient:
    def __init__(self, cfg: dict, session=None, sleep=time.sleep, clock=time.monotonic):
        h = cfg["http"]
        self.cache_dir = Path(cfg["paths"]["cache_dir"])
        self.min_interval = h["min_interval_seconds"]
        self.jitter = h["jitter_seconds"]
        self.timeout = h["timeout_seconds"]
        self.max_retries = h["max_retries"]
        self.backoff_base = h["backoff_base_seconds"]
        self.pause_every = h["pause_every"]
        self.pause_seconds = h["pause_seconds"]
        self.max_consecutive_failures = h["max_consecutive_failures"]
        # session/sleep/clock are injectable so tests can run without the network or real waiting
        self._own_session = session is None  # only a session we created gets replaced on a pause
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self._last_request_at = None
        self.live_requests = 0
        self.cache_hits = 0
        self._consecutive_failures = 0

    # ---------- public API ----------

    def call(self, endpoint_cls, *, max_age_hours=None, refresh=False, require_rows=None, **kwargs):
        """Run an nba_api endpoint through the cache. Returns the loaded endpoint object.

        Usage: client.call(LeagueGameLog, season="2024-25", season_type_all_star="Regular Season")

        max_age_hours: re-download if the cached copy is older than this (use for data that
                       still changes, e.g. current-season aggregates). None = cache forever.
        refresh:       ignore the cache and re-download.
        require_rows:  name of a dataset attribute (e.g. "play_by_play") that must not be empty.
                       If it is, the response is deleted from the cache and FetchError is raised,
                       so an empty answer is never cached as if it were the real thing.
        """
        ep = endpoint_cls(**kwargs, get_request=False)
        text, url = self.get_text(ep.endpoint, ep.parameters, max_age_hours=max_age_hours, refresh=refresh)
        ep.nba_response = NBAStatsResponse(response=text, status_code=200, url=url)
        ep.load_response()
        if require_rows and not getattr(ep, require_rows).get_dict().get("data"):
            self.forget(ep.endpoint, ep.parameters)
            raise FetchError(f"{ep.endpoint} returned no {require_rows} rows for {ep.parameters}")
        return ep

    def forget(self, endpoint: str, params: dict):
        """Delete one cached response."""
        key = cache_key(endpoint, params)
        for suffix in (".json.gz", ".meta.json"):
            (self.cache_dir / endpoint.lower() / f"{key}{suffix}").unlink(missing_ok=True)

    def get_text(self, endpoint: str, params: dict, *, max_age_hours=None, refresh=False):
        """Return (response_text, url), from cache if possible."""
        key = cache_key(endpoint, params)
        body_path = self.cache_dir / endpoint.lower() / f"{key}.json.gz"
        meta_path = body_path.with_name(f"{key}.meta.json")

        if not refresh and body_path.exists() and meta_path.exists() and self._fresh(body_path, max_age_hours):
            self.cache_hits += 1
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            with gzip.open(body_path, "rt", encoding="utf-8") as f:
                return f.read(), meta["url"]

        text, url = self._fetch_with_retries(endpoint, params)
        self._write_cache(body_path, meta_path, endpoint, params, text, url)
        return text, url

    # ---------- internals ----------

    @staticmethod
    def _fresh(path: Path, max_age_hours) -> bool:
        if max_age_hours is None:
            return True
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        return age_hours <= max_age_hours

    def _wait_for_turn(self):
        if self.live_requests and self.pause_every and self.live_requests % self.pause_every == 0:
            log.info("Pausing %ss after %s live requests, then reconnecting.", self.pause_seconds, self.live_requests)
            self.sleep(self.pause_seconds)
            # NBA.com seems to block a long-lived connection, not the machine: start a fresh one.
            if self._own_session:
                self.session.close()
                self.session = requests.Session()
        if self._last_request_at is not None:
            gap = self.min_interval + random.uniform(0, self.jitter)
            elapsed = self.clock() - self._last_request_at
            if elapsed < gap:
                self.sleep(gap - elapsed)

    def _fetch_once(self, endpoint: str, params: dict):
        self._wait_for_turn()
        # Same param handling as nba_api: sorted by key (NBA.com cares), None values dropped.
        sorted_params = sorted(params.items(), key=lambda kv: kv[0])
        try:
            resp = self.session.get(
                BASE_URL.format(endpoint=endpoint),
                params=sorted_params,
                headers=dict(STATS_HEADERS),
                timeout=self.timeout,
            )
        finally:
            self._last_request_at = self.clock()
            self.live_requests += 1
        return resp

    def _fetch_with_retries(self, endpoint: str, params: dict):
        last_problem = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                wait = self.backoff_base * 2 ** (attempt - 1) + random.uniform(0, 1)
                log.warning("Retry %s/%s for %s in %.0fs (%s)", attempt, self.max_retries, endpoint, wait, last_problem)
                self.sleep(wait)
            try:
                resp = self._fetch_once(endpoint, params)
            except (requests.Timeout, requests.ConnectionError) as e:
                # NBA.com usually "blocks" by hanging until timeout, so this is the common failure.
                last_problem = f"{type(e).__name__}"
                continue

            if resp.status_code in RETRYABLE_STATUS:
                last_problem = f"HTTP {resp.status_code}"
                continue
            if resp.status_code != 200:
                # 400/404 etc: our parameters are wrong. Retrying won't help.
                # Not counted toward "blocked": NBA.com answered, it just didn't like the request.
                raise FetchError(f"{endpoint} returned HTTP {resp.status_code} for {params}: {resp.text[:300]}")

            problem = self._validate_body(resp.text)
            if problem:
                last_problem = problem
                continue

            self._consecutive_failures = 0
            return resp.text, resp.url

        self._record_failure()
        raise FetchError(f"{endpoint} failed after {self.max_retries + 1} attempts ({last_problem}) for {params}")

    @staticmethod
    def _validate_body(text: str):
        """Return a description of what's wrong with the body, or None if it looks usable."""
        if '{"Message":"An error has occurred."}' in text:
            return "NBA.com error message in body"
        try:
            data = json.loads(text)
        except ValueError:
            return "body is not valid JSON"
        if not isinstance(data, dict) or not data:
            return "body is empty JSON"
        return None

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.max_consecutive_failures:
            raise BlockedError(
                f"{self._consecutive_failures} requests in a row failed. NBA.com may be throttling you. "
                "Stop for an hour or so, then re-run: everything already downloaded is cached."
            )

    def _write_cache(self, body_path: Path, meta_path: Path, endpoint, params, text, url):
        body_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "endpoint": endpoint,
            "params": params,
            "url": url,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bytes": len(text.encode("utf-8")),
        }
        # Write to a temp file then rename: if the process is killed mid-write we never
        # leave a half-written file that looks like a valid cache entry.
        tmp_body = body_path.with_name(body_path.name + ".tmp")
        with gzip.open(tmp_body, "wt", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_body, body_path)
        tmp_meta = meta_path.with_name(meta_path.name + ".tmp")
        tmp_meta.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        os.replace(tmp_meta, meta_path)

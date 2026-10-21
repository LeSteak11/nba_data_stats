"""Cache -> raw.* tables in DuckDB.

Rules for the raw layer:
  - every value is stored as TEXT exactly as NBA.com sent it (no type guessing, no edits)
  - column names are NBA.com's own
  - each row also gets:  _params      the request parameters, as JSON (e.g. Season, SeasonType, TeamID)
                         _fetched_at  when it was downloaded
                         _cache_key   which cache file it came from
Table name = raw.<endpoint>__<dataset>, e.g. raw.playbyplayv3__play_by_play
V3 endpoints also get raw.<endpoint>__game_header: the response's top-level fields (see _v3_header).
"""

import gzip
import json
import logging
import re
from pathlib import Path

import pandas as pd
from nba_api.stats.library.http import NBAStatsResponse

log = logging.getLogger(__name__)

# endpoint (cache folder name) -> True if it's a "V3" endpoint that needs nba_api's custom parser
RAW_ENDPOINTS = {
    "leaguegamelog": False,
    "commonallplayers": False,
    "playerindex": False,
    "boxscoretraditionalv3": True,
    "boxscoreadvancedv3": True,
    "playbyplayv3": True,
    "boxscorematchupsv3": True,
    "shotchartdetail": False,
    "leaguedashlineups": False,
    "teamplayeronoffdetails": False,
    "leaguestandingsv3": False,
}

# Endpoints a cache may not have yet (added after seasons were already downloaded). If nothing is
# cached, an empty table with the columns the clean SQL uses is created instead.
OPTIONAL = {"leaguestandingsv3": {"standings": ["SeasonID", "TeamID", "Conference", "Division"]}}

FLUSH_EVERY = 300  # files per insert batch (keeps memory low on the big play-by-play load)


def snake(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", name).lower()
    return re.sub(r"_+", "_", s)


def _text(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


def load_all(con, cache_dir: Path):
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    missing = [e for e in RAW_ENDPOINTS if not any((cache_dir / e).glob("*.meta.json"))]
    if [e for e in missing if e not in OPTIONAL]:
        raise RuntimeError(f"No cached data yet for: {', '.join(missing)}. Run the pull first.")
    for endpoint, is_v3 in RAW_ENDPOINTS.items():
        if endpoint in missing:
            for name, cols in OPTIONAL[endpoint].items():
                col_sql = ", ".join(f'"{c}" VARCHAR' for c in cols + ["_params", "_fetched_at", "_cache_key"])
                con.execute(f'CREATE TABLE raw."{endpoint}__{snake(name)}" ({col_sql})')
            print(f"  raw  {endpoint:<24} {0:>6} responses  (none cached yet)")
            continue
        n_files, rows = load_endpoint(con, cache_dir / endpoint, endpoint, is_v3)
        print(f"  raw  {endpoint:<24} {n_files:>6} responses  {rows}")


def load_endpoint(con, folder: Path, endpoint: str, is_v3: bool):
    buffers: dict[str, list[pd.DataFrame]] = {}
    totals: dict[str, int] = {}
    metas = sorted(folder.glob("*.meta.json"))
    for i, meta_path in enumerate(metas, 1):
        key = meta_path.name.removesuffix(".meta.json")
        body_path = folder / f"{key}.json.gz"
        if not body_path.exists():
            continue  # incomplete write from a killed run; will be re-downloaded next pull
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            with gzip.open(body_path, "rt", encoding="utf-8") as f:
                resp = NBAStatsResponse(response=f.read(), status_code=200, url=meta["url"])
        except (OSError, EOFError, ValueError, KeyError):
            # damaged by a shutdown mid-write; the next pull downloads it again
            log.warning("Skipping damaged cache entry %s/%s", endpoint, key)
            continue
        datasets = resp.get_data_sets(endpoint if is_v3 else None)
        if is_v3:
            datasets = {**datasets, "GameHeader": _v3_header(resp.get_dict())}
        params = json.dumps(meta["params"], sort_keys=True)
        for name, ds in datasets.items():
            headers = list(ds["headers"])
            df = pd.DataFrame([[_text(v) for v in row] for row in ds["data"]], columns=headers, dtype=object)
            df["_params"], df["_fetched_at"], df["_cache_key"] = params, meta["fetched_at"], key
            buffers.setdefault(name, []).append(df)
        if i % FLUSH_EVERY == 0:
            _flush(con, endpoint, buffers, totals)
    _flush(con, endpoint, buffers, totals)
    return len(metas), {snake(k): v for k, v in totals.items()}


def _v3_header(body: dict) -> dict:
    """The top-level single values of a V3 response (gameId, homeTeamId, awayTeamId, ...).
    nba_api's parser drops them, but the designated home team of a neutral-site game only lives here."""
    root = next(v for k, v in body.items() if k != "meta" and isinstance(v, dict))
    scalars = {k: v for k, v in root.items() if not isinstance(v, (dict, list))}
    return {"headers": list(scalars), "data": [list(scalars.values())]}


def _flush(con, endpoint, buffers, totals):
    for name, frames in buffers.items():
        table = f'raw."{endpoint}__{snake(name)}"'
        df = pd.concat(frames, ignore_index=True) if frames else None
        if df is None or df.columns.empty:
            continue
        existing = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='raw' AND table_name=?",
            [f"{endpoint}__{snake(name)}"]).fetchall()}
        if not existing:
            cols = ", ".join(f'"{c}" VARCHAR' for c in df.columns)
            con.execute(f"CREATE TABLE {table} ({cols})")
        else:
            # A later response may carry a column earlier ones didn't: add it rather than drop it.
            for c in df.columns:
                if c not in existing:
                    con.execute(f'ALTER TABLE {table} ADD COLUMN "{c}" VARCHAR')
        if len(df):
            con.register("_batch", df)
            con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _batch")
            con.unregister("_batch")
        totals[name] = totals.get(name, 0) + len(df)
    buffers.clear()

"""Offline tests for the HTTP layer: a fake session stands in for NBA.com."""

import json

import pytest
import requests
from nba_api.stats.endpoints import LeagueGameLog

from nbadata.http import BlockedError, FetchError, NBAClient, cache_key

GOOD_BODY = json.dumps({
    "resource": "leaguegamelog",
    "parameters": {},
    "resultSets": [{
        "name": "LeagueGameLog",
        "headers": ["GAME_ID", "TEAM_ID", "PTS"],
        "rowSet": [["0022400061", 1610612747, 110]],
    }],
})


class FakeResponse:
    def __init__(self, status_code=200, text=GOOD_BODY, url="https://stats.nba.com/stats/x?y=1"):
        self.status_code, self.text, self.url = status_code, text, url


class FakeSession:
    """Returns (or raises) the queued items in order and records each call."""

    def __init__(self, *queue):
        self.queue = list(queue)
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append((url, params))
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


@pytest.fixture
def cfg(tmp_path):
    return {
        "paths": {"cache_dir": tmp_path / "cache"},
        "http": {
            "min_interval_seconds": 1.0, "jitter_seconds": 0.0, "timeout_seconds": 5,
            "max_retries": 3, "backoff_base_seconds": 5, "pause_every": 0, "pause_seconds": 0,
            "max_consecutive_failures": 2,
        },
    }


def make_client(cfg, session):
    sleeps = []
    client = NBAClient(cfg, session=session, sleep=sleeps.append, clock=lambda: 0.0)
    return client, sleeps


def test_cache_key_ignores_param_order():
    assert cache_key("x", {"a": 1, "b": 2}) == cache_key("X", {"b": 2, "a": 1})
    assert cache_key("x", {"a": 1}) != cache_key("x", {"a": 2})


def test_second_call_comes_from_cache(cfg):
    session = FakeSession(FakeResponse())
    client, _ = make_client(cfg, session)
    first = client.call(LeagueGameLog, season="2024-25")
    second = client.call(LeagueGameLog, season="2024-25")
    assert len(session.calls) == 1
    assert client.cache_hits == 1
    assert second.league_game_log.get_data_frame().equals(first.league_game_log.get_data_frame())


def test_damaged_cache_entry_is_downloaded_again(cfg):
    """A shutdown mid-write can leave a zero-filled file. It must be re-downloaded, not crash the run."""
    session = FakeSession(FakeResponse(), FakeResponse())
    client, _ = make_client(cfg, session)
    client.call(LeagueGameLog, season="2024-25")
    [meta] = (cfg["paths"]["cache_dir"] / "leaguegamelog").glob("*.meta.json")
    meta.write_bytes(b"\x00" * 300)
    df = client.call(LeagueGameLog, season="2024-25").league_game_log.get_data_frame()
    assert len(session.calls) == 2 and df.loc[0, "PTS"] == 110
    assert json.loads(meta.read_text(encoding="utf-8"))["params"]  # repaired on disk


def test_cache_stores_body_exactly_as_received(cfg):
    import gzip
    client, _ = make_client(cfg, FakeSession(FakeResponse()))
    client.call(LeagueGameLog, season="2024-25")
    [body] = (cfg["paths"]["cache_dir"] / "leaguegamelog").glob("*.json.gz")
    assert gzip.open(body, "rt", encoding="utf-8").read() == GOOD_BODY


def test_retries_transient_errors_then_succeeds(cfg):
    session = FakeSession(requests.Timeout(), FakeResponse(503), FakeResponse(text="not json"), FakeResponse())
    client, sleeps = make_client(cfg, session)
    df = client.call(LeagueGameLog, season="2024-25").league_game_log.get_data_frame()
    assert len(session.calls) == 4
    assert df.loc[0, "PTS"] == 110
    backoffs = [s for s in sleeps if s >= 5]
    assert len(backoffs) == 3 and backoffs[0] < backoffs[1] < backoffs[2]


def test_bad_request_fails_fast_and_is_not_cached(cfg):
    session = FakeSession(FakeResponse(400, text="bad param"))
    client, _ = make_client(cfg, session)
    with pytest.raises(FetchError, match="HTTP 400"):
        client.call(LeagueGameLog, season="2024-25")
    assert len(session.calls) == 1
    assert not (cfg["paths"]["cache_dir"] / "leaguegamelog").exists()


def test_repeated_total_failures_stop_the_run(cfg):
    session = FakeSession(*[requests.ConnectionError()] * 8)
    client, _ = make_client(cfg, session)
    with pytest.raises(FetchError):
        client.call(LeagueGameLog, season="2024-25")
    with pytest.raises(BlockedError):
        client.call(LeagueGameLog, season="2023-24")


def test_rate_limit_waits_between_live_requests(cfg):
    session = FakeSession(FakeResponse(), FakeResponse())
    client, sleeps = make_client(cfg, session)
    client.call(LeagueGameLog, season="2024-25")
    client.call(LeagueGameLog, season="2023-24")
    assert sleeps == [1.0]  # clock is frozen at 0, so the full 1.0s gap is enforced


def test_response_time_counts_toward_the_gap(cfg):
    """Gap is start-to-start: a 0.4 s answer leaves only 0.6 s to wait for a 1.0 s gap."""
    now = [0.0]

    class SlowSession(FakeSession):
        def get(self, *args, **kwargs):
            now[0] += 0.4
            return super().get(*args, **kwargs)

    sleeps = []
    client = NBAClient(cfg, session=SlowSession(FakeResponse(), FakeResponse()), sleep=sleeps.append,
                       clock=lambda: now[0])
    client.call(LeagueGameLog, season="2024-25")
    client.call(LeagueGameLog, season="2023-24")
    assert sleeps == [pytest.approx(0.6)]


def test_unparseable_response_is_a_failure_not_a_crash(cfg):
    """NBA.com sometimes sends a V3 box score with an empty team shell; nba_api's parser crashes on it."""
    from nba_api.stats.endpoints import BoxScoreMatchupsV3
    empty_team = {"teamId": 0, "teamCity": None, "teamName": None, "teamTricode": None, "teamSlug": None,
                  "players": []}
    body = json.dumps({"meta": {}, "boxScoreMatchups": {"gameId": "0022300383", "awayTeamId": 1610612743,
                       "homeTeamId": 1610612751, "homeTeam": empty_team, "awayTeam": empty_team}})
    client, _ = make_client(cfg, FakeSession(FakeResponse(text=body)))
    with pytest.raises(FetchError, match="could not be parsed"):
        client.call(BoxScoreMatchupsV3, game_id="0022300383")
    assert not list((cfg["paths"]["cache_dir"] / "boxscorematchupsv3").glob("*"))  # not kept in the cache


def test_max_age_forces_refetch(cfg):
    session = FakeSession(FakeResponse(), FakeResponse())
    client, _ = make_client(cfg, session)
    client.call(LeagueGameLog, season="2024-25")
    client.call(LeagueGameLog, season="2024-25", max_age_hours=-1)  # anything is "too old"
    assert len(session.calls) == 2


def test_timeout_retries_on_a_fresh_connection(cfg, monkeypatch):
    """A blocked connection hangs until timeout; the retry must not go out on that same connection."""
    sessions = [FakeSession(requests.Timeout()), FakeSession(FakeResponse())]
    monkeypatch.setattr(requests, "Session", lambda: sessions.pop(0))
    events = []
    client = NBAClient(cfg, sleep=lambda s: None, clock=lambda: 0.0)
    client.on_event = lambda kind, **info: events.append(kind)
    first = client.session
    client.call(LeagueGameLog, season="2024-25")
    assert client.session is not first and len(first.calls) == 1 and len(client.session.calls) == 1
    assert events == ["reconnect", "retry", "live"]
    assert client.retries == 1


def test_pause_opens_a_fresh_connection(cfg):
    """Every pause_every live requests: pause, then replace the session (NBA.com blocks long-lived ones)."""
    cfg["http"].update(pause_every=2, pause_seconds=30)
    sleeps = []
    client = NBAClient(cfg, sleep=sleeps.append, clock=lambda: 0.0)  # no session passed -> client owns it
    first = client.session
    client.live_requests = 2
    client._wait_for_turn()
    assert 30 in sleeps
    assert client.session is not first

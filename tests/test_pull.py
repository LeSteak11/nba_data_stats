"""The pull step against a fake NBA.com, then a build on what it downloaded."""

import json
from urllib.parse import urlparse

import duckdb

from nbadata.db import build_db
from nbadata.http import NBAClient
from nbadata.pull import run_pull
from tests import fake_cache as fc


class FakeNBA:
    """Answers like stats.nba.com, using the fixture bodies from fake_cache."""

    def __init__(self):
        self.requests = []

    def get(self, url, params, headers, timeout):
        endpoint = urlparse(url).path.rsplit("/", 1)[-1]
        p = dict(params)
        self.requests.append((endpoint, p))
        body = self.body(endpoint, p)
        return type("R", (), {"status_code": 200, "text": json.dumps(body), "url": url})()

    def body(self, endpoint, p):
        cap, pix = fc.players_lists()
        if endpoint == "leaguegamelog":
            rows = fc.gamelog_rows() if p["SeasonType"] == "Regular Season" else []
            return fc.legacy("LeagueGameLog", fc.GL_HEADERS, rows)
        return {
            "commonallplayers": lambda: cap,
            "playerindex": lambda: pix,
            "boxscoretraditionalv3": fc.box_traditional,
            "boxscoreadvancedv3": fc.box_advanced,
            "playbyplayv3": fc.pbp,
            "boxscorematchupsv3": fc.matchups,
            "shotchartdetail": lambda: fc.shots()[int(p["TeamID"])],
            "leaguedashlineups": lambda: fc.lineups(int(p["TeamID"]), p["MeasureType"]),
            "teamplayeronoffdetails": lambda: fc.on_off(int(p["TeamID"])),
            "leaguestandingsv3": fc.standings,
        }[endpoint]()


def test_pull_then_build(tmp_path):
    cfg = {
        "paths": {"data_dir": tmp_path, "cache_dir": tmp_path / "cache", "log_dir": tmp_path / "logs"},
        "seasons": {"completed": ["2024-25"], "current": "2026-27", "season_types": ["Regular Season", "PlayIn", "Playoffs"]},
        "http": {"min_interval_seconds": 0, "jitter_seconds": 0, "timeout_seconds": 5, "max_retries": 0,
                 "backoff_base_seconds": 0, "pause_every": 0, "pause_seconds": 0, "max_consecutive_failures": 3},
    }
    nba = FakeNBA()
    client = NBAClient(cfg, session=nba, sleep=lambda s: None)
    assert run_pull(cfg, ["2024-25"], client=client) == 0
    # 2 player lists + standings + 3 game logs + 2 teams x 4 team-level calls + 1 game x 4 per-game calls
    assert len(nba.requests) == 2 + 1 + 3 + 8 + 4
    lineup_calls = [p for e, p in nba.requests if e == "leaguedashlineups"]
    assert all(p["TeamID"] in (fc.BOS, fc.NYK) for p in lineup_calls)  # per team, never league-wide

    # Second pull: everything is cached, nothing is re-requested
    assert run_pull(cfg, ["2024-25"], client=client) == 0
    assert len(nba.requests) == 18

    assert build_db(cfg) == 0
    with duckdb.connect(str(tmp_path / "nba.duckdb"), read_only=True) as con:
        assert con.sql("SELECT count(*) FROM clean.shots").fetchone()[0] == 63


def _cfg(tmp_path):
    return {
        "paths": {"data_dir": tmp_path, "cache_dir": tmp_path / "cache", "log_dir": tmp_path / "logs"},
        "seasons": {"completed": ["2005-06", "2024-25"], "current": "2026-27",
                    "season_types": ["Regular Season", "PlayIn", "Playoffs"]},
        "http": {"min_interval_seconds": 0, "jitter_seconds": 0, "timeout_seconds": 5, "max_retries": 0,
                 "backoff_base_seconds": 0, "pause_every": 0, "pause_seconds": 0, "max_consecutive_failures": 3},
    }


def test_old_season_skips_datasets_nba_com_does_not_have(tmp_path):
    """Before 2007-08 there are no lineups or on/off; before 2017-18 no matchups. Don't ask for them."""
    nba = FakeNBA()
    run_pull(_cfg(tmp_path), ["2005-06"], client=NBAClient(_cfg(tmp_path), session=nba, sleep=lambda s: None))
    asked = {e for e, _ in nba.requests}
    assert not asked & {"leaguedashlineups", "teamplayeronoffdetails", "boxscorematchupsv3"}
    # 2 player lists + standings + 3 game logs + 2 teams x 1 shot chart + 1 game x 3 per-game calls
    assert len(nba.requests) == 2 + 1 + 3 + 2 + 3


def test_empty_matchups_is_recorded_as_a_known_gap(tmp_path, monkeypatch):
    import nbadata.checks as checks
    gaps_file = tmp_path / "known_gaps.toml"
    monkeypatch.setattr(checks, "KNOWN_GAPS_PATH", gaps_file)
    empty_team = {"teamId": 0, "players": []}

    class EmptyMatchups(FakeNBA):
        def body(self, endpoint, p):
            if endpoint == "boxscorematchupsv3":
                return {"meta": {}, "boxScoreMatchups": {"gameId": fc.GAME, "homeTeam": empty_team,
                                                         "awayTeam": empty_team}}
            return super().body(endpoint, p)

    cfg = _cfg(tmp_path)
    assert run_pull(cfg, ["2024-25"], client=NBAClient(cfg, session=EmptyMatchups(), sleep=lambda s: None)) == 1
    [gap] = checks.load_known_gaps()
    assert gap["check"] == "game_completeness" and gap["row"]["game_id"] == fc.GAME
    assert gap["row"]["missing_matchups"] is True and gap["row"]["missing_pbp"] is False
    # recorded once, not again on a re-run
    run_pull(cfg, ["2024-25"], client=NBAClient(cfg, session=EmptyMatchups(), sleep=lambda s: None))
    assert len(checks.load_known_gaps()) == 1


def test_next_season_is_newest_unfinished_and_skips_the_current_one():
    from nbadata.pull import next_season
    cfg = {"seasons": {"completed": ["2019-20", "2020-21", "2021-22"], "current": "2026-27"}}
    done = {"complete": True}
    todo = {"complete": False}
    assert next_season(cfg, {"2019-20": todo, "2020-21": todo, "2021-22": done, "2026-27": todo}) == "2020-21"
    assert next_season(cfg, {"2019-20": done, "2020-21": done, "2021-22": done, "2026-27": todo}) is None

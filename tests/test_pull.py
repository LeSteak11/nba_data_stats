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
        }[endpoint]()


def test_pull_then_build(tmp_path):
    cfg = {
        "paths": {"data_dir": tmp_path, "cache_dir": tmp_path / "cache", "log_dir": tmp_path / "logs"},
        "seasons": {"current": "2026-27", "season_types": ["Regular Season", "PlayIn", "Playoffs"]},
        "http": {"min_interval_seconds": 0, "jitter_seconds": 0, "timeout_seconds": 5, "max_retries": 0,
                 "backoff_base_seconds": 0, "pause_every": 0, "pause_seconds": 0, "max_consecutive_failures": 3},
    }
    nba = FakeNBA()
    client = NBAClient(cfg, session=nba, sleep=lambda s: None)
    assert run_pull(cfg, ["2024-25"], client=client) == 0
    # 2 player lists + 3 game logs + 2 teams x 4 team-level calls + 1 game x 4 per-game calls
    assert len(nba.requests) == 2 + 3 + 8 + 4
    lineup_calls = [p for e, p in nba.requests if e == "leaguedashlineups"]
    assert all(p["TeamID"] in (fc.BOS, fc.NYK) for p in lineup_calls)  # per team, never league-wide

    # Second pull: everything is cached, nothing is re-requested
    assert run_pull(cfg, ["2024-25"], client=client) == 0
    assert len(nba.requests) == 17

    assert build_db(cfg) == 0
    with duckdb.connect(str(tmp_path / "nba.duckdb"), read_only=True) as con:
        assert con.sql("SELECT count(*) FROM clean.shots").fetchone()[0] == 63

"""End-to-end: fake cache -> raw -> clean -> checks, entirely offline."""

import duckdb
import pytest

from nbadata.db import build_db
from tests.fake_cache import BOS, GAME, NYK, build_fake_cache


@pytest.fixture
def cfg(tmp_path):
    return {"paths": {"data_dir": tmp_path, "cache_dir": tmp_path / "cache"}}


def query(cfg, sql):
    with duckdb.connect(str(cfg["paths"]["data_dir"] / "nba.duckdb"), read_only=True) as con:
        return con.sql(sql).fetchall()


def test_clean_build_passes_every_check(cfg):
    build_fake_cache(cfg["paths"]["cache_dir"])
    assert build_db(cfg) == 0
    failing = query(cfg, "SELECT check_name FROM meta.dq_results WHERE NOT passed")
    # one fake game instead of 1230 -> the (warn-level) season game count check flags it, correctly
    assert failing == [("regular_season_game_count",)]


def test_clean_values_are_typed_and_parsed(cfg):
    build_fake_cache(cfg["paths"]["cache_dir"])
    build_db(cfg)
    assert query(cfg, "SELECT home_team_id, home_pts, away_pts, periods, season_type FROM clean.games") == \
        [(BOS, 50, 40, 4, "Regular Season")]
    assert query(cfg, "SELECT seconds_played, is_starter FROM clean.player_box WHERE player_id = 1628369") == \
        [(2880, True)]
    assert query(cfg, "SELECT matchup_seconds FROM clean.matchups LIMIT 1") == [(323,)]
    assert query(cfg, "SELECT height_inches, draft_year FROM clean.players WHERE player_id = 1628973") == [(80, None)]
    assert query(cfg, f"SELECT player_ids FROM clean.lineups WHERE team_id = {BOS}") == \
        [([1627759, 1628369],)]
    # action 1: 'PT10M01.00S' in period 1 -> 720 - 601 = 119 s elapsed
    # action 2: 'PT09M02.00S' in period 2 -> 720 + 720 - 542 = 898 s elapsed
    assert query(cfg, "SELECT game_seconds_elapsed FROM clean.pbp_events WHERE action_number <= 2 ORDER BY 1") == \
        [(119.0,), (898.0,)]


def test_planted_box_score_error_is_reported(cfg):
    def mutate(endpoint, body):
        if endpoint == "boxscoretraditionalv3":
            body["boxScoreTraditional"]["homeTeam"]["players"][0]["statistics"]["points"] += 2

    build_fake_cache(cfg["paths"]["cache_dir"], mutate)
    assert build_db(cfg) == 1
    failing = dict(query(cfg, "SELECT check_name, n_offending FROM meta.dq_results WHERE NOT passed"))
    failing.pop("regular_season_game_count")
    assert failing == {"player_sum_equals_team_box": 1}
    assert (cfg["paths"]["data_dir"] / "dq" / "player_sum_equals_team_box.csv").exists()


def test_planted_missing_matchups_is_reported(cfg):
    build_fake_cache(cfg["paths"]["cache_dir"])
    for f in (cfg["paths"]["cache_dir"] / "boxscorematchupsv3").glob("*"):
        f.unlink()
    # one other game's matchups stand in so the endpoint isn't empty; ours is now missing
    from tests.fake_cache import matchups, write_entry
    other = matchups()
    other["boxScoreMatchups"]["gameId"] = "0022400099"
    write_entry(cfg["paths"]["cache_dir"], "boxscorematchupsv3", {"GameID": "0022400099"}, other)
    build_db(cfg)
    failing = dict(query(cfg, "SELECT check_name, n_offending FROM meta.dq_results WHERE NOT passed"))
    assert failing.get("game_completeness") == 1


def test_real_nbacom_quirks_are_handled(cfg):
    """Four quirks found in the real 2024-25 data. With all of them planted, every check still passes."""
    def mutate(endpoint, body):
        if endpoint == "leaguegamelog" and body["resultSets"][0]["rowSet"]:
            bos = body["resultSets"][0]["rowSet"][0]
            bos[6] = "BOS @ NYK"  # 1. neutral site: both teams listed as '@'
            bos[24] += 1          # 4. official TOV includes the team turnover planted below
        if endpoint == "boxscoreadvancedv3":
            # 2. same player twice, other spelling, blank minutes
            p = body["boxScoreAdvanced"]["homeTeam"]["players"]
            p.append(dict(p[0], familyName=p[0]["familyName"] + " Jr.", statistics=dict(p[0]["statistics"], minutes="")))
        if endpoint == "playbyplayv3":
            actions = body["game"]["actions"]
            last, n = actions[-1], len(actions)
            miss = next(a for a in actions if a["actionType"] == "Missed Shot" and a["teamId"] == BOS)
            # 3. a block shares the missed shot's actionNumber
            actions.append(dict(miss, actionId=n + 1, teamId=NYK, teamTricode="NYK", personId=1626157,
                                playerName="Towns", actionType="", subType="", isFieldGoal=0, shotValue=0,
                                shotResult="", description="Towns BLOCK (1 BLK)", location="v"))
            # 4. team turnover: teamId 0, the team's id in personId
            actions.append(dict(last, actionNumber=n + 2, actionId=n + 2, teamId=0, teamTricode="", personId=BOS,
                                playerName="", actionType="Turnover", subType="Shot Clock Turnover", isFieldGoal=0,
                                shotValue=0, shotResult="", description="CELTICS Turnover: Shot Clock (T#1)",
                                location="h"))

    build_fake_cache(cfg["paths"]["cache_dir"], mutate)
    assert build_db(cfg) == 0
    assert query(cfg, "SELECT check_name FROM meta.dq_results WHERE NOT passed") == [("regular_season_game_count",)]
    assert query(cfg, "SELECT home_team_id, is_neutral_site FROM clean.games") == [(BOS, True)]
    assert query(cfg, f"SELECT tov, team_tov, tov_total FROM clean.team_box WHERE team_id = {BOS}") == [(0, 1, 1)]
    assert query(cfg, "SELECT team_id, player_id FROM clean.pbp_events WHERE is_team_event") == [(BOS, None)]
    assert query(cfg, "SELECT count(*) FROM clean.player_box") == [(4,)]


def test_on_off_rules(cfg):
    """Off-court-only roster players and traded players are fine; on-court minutes that don't
    match the box score are not. A box-score player missing from the player lists gets a stub row."""
    def mutate(endpoint, body):
        if endpoint == "teamplayeronoffdetails" and body["resultSets"][0]["rowSet"][0][1] == BOS:
            on, off = body["resultSets"][0], body["resultSets"][2]
            on["rowSet"][0][11] = 40.0                                     # Tatum: box says 48
            off["rowSet"].append(list(off["rowSet"][0][:4]) + [999, "Injured, Guy"] + off["rowSet"][0][6:])
            off["rowSet"][-1][11] = 48.0                                   # never played: off only
        if endpoint == "commonallplayers":
            body["resultSets"][0]["rowSet"] = [r for r in body["resultSets"][0]["rowSet"] if r[0] != 1626157]

    build_fake_cache(cfg["paths"]["cache_dir"], mutate)
    build_db(cfg)
    failing = dict(query(cfg, "SELECT check_name, n_offending FROM meta.dq_results WHERE NOT passed"))
    failing.pop("regular_season_game_count")
    assert failing == {"on_off_on_minutes_equal_box": 1, "players_have_bio": 1}
    assert query(cfg, "SELECT player_name, in_player_list FROM clean.players WHERE player_id = 1626157") == \
        [("Karl-Anthony Towns", False)]


def test_failed_build_keeps_previous_database(cfg):
    build_fake_cache(cfg["paths"]["cache_dir"])
    build_db(cfg)

    def mutate(endpoint, body):
        if endpoint == "leaguegamelog" and body["resultSets"][0]["rowSet"]:
            body["resultSets"][0]["rowSet"][0][26] = "not a number"  # PTS
    build_fake_cache(cfg["paths"]["cache_dir"], mutate)
    with pytest.raises(RuntimeError, match="03_games.sql"):
        build_db(cfg)
    assert query(cfg, "SELECT count(*) FROM clean.games") == [(1,)]


def test_smoke_test_leftovers_are_ignored(cfg):
    """The smoke test caches a single-game shot chart and a league-wide (capped) lineup list.
    Neither may leak into the clean tables as duplicates."""
    from tests.fake_cache import ST, SEASON, lineups, shots, write_entry
    build_fake_cache(cfg["paths"]["cache_dir"])
    write_entry(cfg["paths"]["cache_dir"], "shotchartdetail",
                {"TeamID": BOS, "Season": SEASON, "SeasonType": ST, "ContextMeasure": "FGA", "GameID": GAME},
                shots()[BOS])
    for m in ("Base", "Advanced"):
        write_entry(cfg["paths"]["cache_dir"], "leaguedashlineups",
                    {"TeamID": "", "Season": SEASON, "SeasonType": ST, "MeasureType": m}, lineups(BOS, m))
    assert build_db(cfg) == 0
    assert query(cfg, "SELECT count(*) FROM clean.lineups") == [(2,)]
    assert query(cfg, "SELECT count(*) FROM clean.shots") == [(63,)]

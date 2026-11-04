"""The hub, offline: a tiny database built from the fake cache, plus a few hand-made rows."""

import contextlib
import io
import os
import shutil
import subprocess
import sys

import duckdb
import pytest

from nbadata.db import _patiently, build_db
from nbadata.hub import queries as Q
from nbadata.hub.app import create_app
from nbadata.hub.data import available
from nbadata.hub.images import find_image
from tests.fake_cache import BOS, NYK, build_fake_cache

TRADED, NO_GAMES = 999001, 999002


@pytest.fixture(scope="session")
def base_db(tmp_path_factory):
    """Fake-cache build + a traded player (BOS then NYK, one DNP) + a player with no games."""
    d = tmp_path_factory.mktemp("hubdb")
    build_fake_cache(d / "cache")
    with contextlib.redirect_stdout(io.StringIO()):
        build_db({"paths": {"data_dir": d, "cache_dir": d / "cache"}})
    path = d / "nba.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute(f"""
            INSERT INTO clean.players BY NAME
            SELECT * FROM (VALUES ({TRADED}, 'Tëst Tradé-Guy', 'F', 80, 220, 2019, 2025, true, 2019, 1, 7),
                                  ({NO_GAMES}, 'Nobody Played', 'G', 75, 190, 2001, 2003, true, NULL, NULL, NULL))
              t(player_id, player_name, position, height_inches, weight_lbs, from_year, to_year, has_bio,
                draft_year, draft_round, draft_number);
            INSERT INTO clean.games BY NAME
            SELECT * FROM (VALUES ('0022400100', DATE '2024-11-01', {BOS}, {NYK}, 100, 90, {BOS}),
                                  ('0022400101', DATE '2024-11-03', {NYK}, {BOS}, 95, 99, {BOS}),
                                  ('0022400102', DATE '2025-01-10', {NYK}, {BOS}, 110, 100, {NYK}))
              t(game_id, game_date, home_team_id, away_team_id, home_pts, away_pts, winner_team_id),
              (SELECT '2024-25' AS season, 'Regular Season' AS season_type, false AS is_neutral_site,
                      4 AS periods, false AS is_overtime);
            INSERT INTO clean.player_box BY NAME
            SELECT *, '2024-25' AS season, 'Regular Season' AS season_type, {TRADED} AS player_id,
                   'Tëst Tradé-Guy' AS player_name, 0 AS oreb, 0 AS pf
            FROM (VALUES ('0022400100', DATE '2024-11-01', {BOS}, {NYK}, true, true, true, 600, 10, 4, 8, 0, 2, 2, 2, 5, 1, 0, 0, 1, 3, NULL),
                         ('0022400101', DATE '2024-11-03', {BOS}, {NYK}, false, false, false, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'DNP - Coach''s Decision'),
                         ('0022400102', DATE '2025-01-10', {NYK}, {BOS}, true, true, true, 1200, 20, 8, 10, 1, 3, 3, 4, 7, 2, 1, 1, 2, 9, NULL))
              t(game_id, game_date, team_id, opponent_team_id, is_home, is_starter, played, seconds_played, pts, fgm,
                fga, fg3m, fg3a, ftm, fta, reb, ast, stl, blk, tov, plus_minus, status_comment);
        """)
    return path


@pytest.fixture
def db_file(base_db, tmp_path):
    """A private copy, for tests that swap or lock the file."""
    p = tmp_path / "nba.duckdb"
    shutil.copy(base_db, p)
    return p


def client_for(path, images_dir=None, wait_s=0.3):
    app = create_app(path, images_dir or path.parent / "images", wait_s=wait_s)
    return app.test_client()


@pytest.fixture
def client(base_db):
    return client_for(base_db)


# --- numbers -----------------------------------------------------------------------------------------

def test_traded_player_gets_a_row_per_team_and_a_tot_row(base_db):
    with duckdb.connect(str(base_db), read_only=True) as con:
        rs = Q.player_seasons(con, TRADED)["Regular Season"]
    bos, nyk, tot = rs["rows"]
    assert (bos["team"], nyk["team"], tot["team"]) == ("BOS", "NYK", "TOT")
    assert (bos["gp"], bos["gs"], bos["pts"]) == (1, 1, 10)  # the DNP doesn't count as a game
    assert (tot["gp"], tot["gs"], tot["pts"], tot["pts_pg"]) == (2, 2, 30, 15.0)
    assert tot["fg_pct"] == pytest.approx(12 / 18)         # from totals, not the average of 50% and 80%
    assert tot["fg3_pct"] == pytest.approx(1 / 5)
    assert tot["min_pg"] == pytest.approx(15.0)
    assert tot["ts_pct"] == pytest.approx(30 / (2 * (18 + 0.44 * 6)))
    assert rs["career"]["gp"] == 2 and rs["career"]["pts"] == 30


def test_game_log_sorting_and_dnp(base_db):
    with duckdb.connect(str(base_db), read_only=True) as con:
        by_date = Q.game_log(con, TRADED, "2024-25", "Regular Season")
        by_pts = Q.game_log(con, TRADED, "2024-25", "Regular Season", "pts", desc=True)
        bogus = Q.game_log(con, TRADED, "2024-25", "Regular Season", "pts; DROP TABLE x")
    assert [r["game_id"][-3:] for r in by_date] == ["100", "101", "102"]
    assert [r["pts"] for r in by_pts] == [20, 10, None]     # DNP last
    assert [r["game_id"] for r in bogus] == [r["game_id"] for r in by_date]
    assert by_date[0]["won"] and by_date[0]["margin"] == 10 and by_date[0]["opp"] == "NYK"
    assert by_date[1]["status_comment"].startswith("DNP")


def test_search_ignores_accents_case_and_punctuation(base_db):
    assert Q.normalize("Nikola Jokić") == "nikola jokic"
    assert Q.normalize("D'Angelo Russell") == "dangelo russell"
    with duckdb.connect(str(base_db), read_only=True) as con:
        index = Q.player_index(con)
    for q in ("test trade", "TRADÉ-guy", "tradeguy", "tëst"):
        assert [r["player_id"] for r in Q.search_players(index, q)] == [TRADED], q
    names = [r["player_name"] for r in Q.search_players(index, "ja")]
    assert set(names[:3]) == {"Jaylen Brown", "Jayson Tatum", "Jalen Brunson"}  # first-name matches before "...ja..." inside a name
    assert Q.search_players(index, "jayson tatum")[0]["rank"] == 0
    assert Q.search_players(index, "   ") == []


def test_available():
    first = {"lineups": "2007-08", "matchups": "2017-18"}
    assert not available(first, "lineups", "2006-07")
    assert available(first, "lineups", "2007-08")
    assert available(first, "shots", "2000-01")  # not listed = every season


# --- pages -------------------------------------------------------------------------------------------

def test_pages_render(client):
    for url in ["/", "/search", "/search?q=ja", f"/player/{TRADED}", f"/player/{TRADED}?mode=totals",
                f"/player/{TRADED}/games", f"/player/{TRADED}/games?season=2024-25&type=Regular+Season&sort=pts&dir=desc",
                f"/player/{TRADED}/games?season=1900-01&type=nope&sort=nope", "/teams", "/leaders", "/health"]:
        assert client.get(url).status_code == 200, url
    page = client.get(f"/player/{TRADED}").get_data(as_text=True)
    assert "TOT" in page and "Tëst Tradé-Guy" in page


def test_search_redirects_on_a_single_match(client):
    r = client.get("/search?q=tatum")
    assert r.status_code == 302 and r.location.endswith("/player/1628369")
    hits = client.get("/api/search?q=brun").get_json()
    assert hits[0]["name"] == "Jalen Brunson" and hits[0]["url"] == "/player/1628973"


def test_player_without_games_and_unknown_ids(client):
    page = client.get(f"/player/{NO_GAMES}").get_data(as_text=True)
    assert "No games in the database yet" in page and "2024-25" in page
    assert client.get(f"/player/{NO_GAMES}/games").status_code == 200
    assert client.get("/player/123").status_code == 404
    assert client.get("/player/123/games").status_code == 404


# --- living next to a season run ----------------------------------------------------------------------

def test_page_loads_dont_block_build_db_swapping_the_file(db_file, base_db, tmp_path):
    client = client_for(db_file)
    assert client.get("/api/search?q=trade").get_json()
    assert client.get(f"/player/{TRADED}").status_code == 200

    # What build-db does at the end: a fresh file replaces nba.duckdb. Must work right after page loads.
    fresh = tmp_path / "fresh.duckdb"
    shutil.copy(base_db, fresh)
    with duckdb.connect(str(fresh)) as con:
        con.execute(f"DELETE FROM clean.player_box WHERE player_id = {TRADED}")
        con.execute(f"DELETE FROM clean.players WHERE player_id = {TRADED}")
    os.utime(fresh, ns=(1, 1))  # a different modified time, like a new build
    os.replace(fresh, db_file)

    # ...and the hub picks up the new file (cached search names included) on the next page load.
    assert client.get("/api/search?q=trade").get_json() == []
    assert client.get(f"/player/{TRADED}").status_code == 404


def test_busy_page_while_the_database_is_open_for_writing(db_file):
    holder = subprocess.Popen(
        [sys.executable, "-c", f"import duckdb, time; c = duckdb.connect({str(db_file)!r}); print('open', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "open"
        r = client_for(db_file).get("/")
        assert r.status_code == 503 and "being updated" in r.get_data(as_text=True)
    finally:
        holder.kill()
        holder.wait()


def test_missing_database(tmp_path):
    r = client_for(tmp_path / "nba.duckdb").get("/")
    assert r.status_code == 503 and "run_next_season.bat" in r.get_data(as_text=True)


def test_patient_retry_waits_then_gives_up():
    calls = []

    def flaky(n_failures):
        def action():
            calls.append(1)
            if len(calls) <= n_failures:
                raise PermissionError("in use")
            return "done"
        return action

    with contextlib.redirect_stdout(io.StringIO()):
        assert _patiently(flaky(3), PermissionError, "nba.duckdb", wait_s=60, sleep=lambda s: None) == "done"
        assert len(calls) == 4
        calls.clear()
        with pytest.raises(PermissionError):
            _patiently(flaky(10**9), PermissionError, "nba.duckdb", wait_s=0, sleep=lambda s: None)


# --- images ------------------------------------------------------------------------------------------

def test_image_lookup_and_placeholders(base_db, tmp_path):
    images = tmp_path / "images"
    (images / "players").mkdir(parents=True)
    (images / "teams").mkdir()
    assert find_image(images, "player", 1628369) is None
    (images / "players" / "1628369.webp").write_bytes(b"x")
    (images / "teams" / f"{BOS}.png").write_bytes(b"x")
    (images / "teams" / f"{BOS}_2007-08.svg").write_bytes(b"x")
    assert find_image(images, "player", 1628369).name == "1628369.webp"
    assert find_image(images, "team", BOS, "2007-08").name == f"{BOS}_2007-08.svg"
    assert find_image(images, "team", BOS, "2024-25").name == f"{BOS}.png"

    client = client_for(base_db, images)
    assert client.get("/img/player/1628369").data == b"x"
    placeholder = client.get("/img/player/1")
    assert placeholder.mimetype == "image/svg+xml" and placeholder.headers["Cache-Control"] == "no-cache"
    team = client.get("/img/team/1?abbr=<b>").get_data(as_text=True)
    assert "&lt;b&gt;" in team and "<b>" not in team

"""Data-quality checks.

Each check is a SQL query that returns the OFFENDING rows (empty result = pass).
Nothing is skipped silently: every check's result goes into meta.dq_results, and every
offending row is written to data/dq/<check>.csv so you can look at exactly what's wrong.

severity:  error = the data is wrong or missing; don't trust analysis that touches it
           warn  = worth knowing about, often a known quirk of NBA.com's data

Rows listed in known_gaps.toml are NBA.com's own gaps: they don't fail a check, but are still
counted (meta.dq_results.n_known) and written to data/dq/<check>.known.csv.

Some datasets only exist from a certain season (lineups, on/off, matchups: see config.toml
[datasets]). Checks read meta.datasets and don't expect them for earlier seasons.
"""

import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KNOWN_GAPS_PATH = Path(__file__).parent / "known_gaps.toml"

# Regular-season games per season when it isn't 1230 (30 teams x 82 / 2).
SHORT_SEASONS = {"1998-99": 725, "2011-12": 990, "2019-20": 1059, "2020-21": 1080}
TWENTY_NINE_TEAMS_UNTIL = "2004-05"  # Charlotte Bobcats joined in 2004-05: 29 x 82 / 2 = 1189 before


def _first(dataset: str) -> str:
    """SQL for the first season NBA.com has an optional dataset (from meta.datasets)."""
    return f"(SELECT first_season FROM meta.datasets WHERE dataset = '{dataset}')"


STAT_COLS = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf"]


@dataclass
class Check:
    name: str
    severity: str
    description: str
    sql: str


def _cmp(left: str, right: str, cols: list[str]) -> str:
    """WHERE clause fragment: any column differs between two aliases (NULL-safe)."""
    return " OR ".join(f"{left}.{c} IS DISTINCT FROM {right}.{c}" for c in cols)


CHECKS = [
    # ---------- structure ----------
    Check("games_unique", "error", "Each game_id appears once in clean.games",
          "SELECT game_id, count(*) AS n FROM clean.games GROUP BY 1 HAVING n > 1"),
    Check("games_paired", "error",
          "Every game in the official game log ended up in clean.games (2 team rows -> 1 game)",
          """SELECT DISTINCT GAME_ID AS game_id FROM raw.leaguegamelog__league_game_log
             WHERE GAME_ID NOT IN (SELECT game_id FROM clean.games)"""),
    Check("game_id_matches_season_type", "error",
          "Game id prefix agrees with season type (002 = regular, 005 = play-in, 004 = playoffs)",
          """SELECT game_id, season_type FROM clean.games
             WHERE NOT ((season_type = 'Regular Season' AND game_id LIKE '002%')
                     OR (season_type = 'PlayIn' AND game_id LIKE '005%')
                     OR (season_type = 'Playoffs' AND game_id LIKE '004%'))"""),
    Check("player_box_unique", "error", "One row per player per game",
          "SELECT game_id, player_id, count(*) AS n FROM clean.player_box GROUP BY ALL HAVING n > 1"),
    Check("team_box_unique", "error", "One row per team per game, exactly 2 teams per game",
          "SELECT game_id, count(*) AS n FROM clean.team_box GROUP BY 1 HAVING n <> 2"),
    Check("pbp_unique", "error", "One row per game per action_id (action_number repeats for steals/blocks)",
          "SELECT game_id, action_id, count(*) AS n FROM clean.pbp_events GROUP BY ALL HAVING n > 1"),
    Check("lineups_unique", "error", "One row per lineup per team per season type",
          "SELECT season, season_type, team_id, lineup_id, count(*) AS n FROM clean.lineups GROUP BY ALL HAVING n > 1"),
    Check("box_rows_have_game", "error", "Every box score / play-by-play row belongs to a known game",
          """SELECT 'player_box' AS tbl, game_id FROM clean.player_box WHERE season IS NULL
             UNION SELECT 'team_box', game_id FROM clean.team_box WHERE season IS NULL
             UNION SELECT 'pbp_events', game_id FROM clean.pbp_events WHERE season IS NULL
             UNION SELECT 'matchups', game_id FROM clean.matchups WHERE season IS NULL"""),

    # ---------- completeness ----------
    Check("game_completeness", "error",
          "Every game has traditional box, advanced box, play-by-play and (from 2017-18) matchup data",
          f"""SELECT g.game_id, g.season, g.season_type,
                    g.game_id NOT IN (SELECT gameId FROM raw.boxscoretraditionalv3__player_stats) AS missing_traditional,
                    g.game_id NOT IN (SELECT gameId FROM raw.boxscoreadvancedv3__player_stats)    AS missing_advanced,
                    g.game_id NOT IN (SELECT gameId FROM raw.playbyplayv3__play_by_play)          AS missing_pbp,
                    g.game_id NOT IN (SELECT gameId FROM raw.boxscorematchupsv3__player_stats)
                        AND g.season >= {_first("matchups")}                                      AS missing_matchups
             FROM clean.games g
             WHERE missing_traditional OR missing_advanced OR missing_pbp OR missing_matchups"""),
    Check("team_season_coverage", "error",
          "Every team that played in a season type has shots, plus lineups and on/off data (from 2007-08)",
          f"""WITH ts AS (SELECT DISTINCT season, season_type, team_id FROM clean.team_box)
             SELECT ts.*,
                 NOT EXISTS (SELECT 1 FROM clean.shots s WHERE s.season = ts.season AND s.season_type = ts.season_type AND s.team_id = ts.team_id) AS missing_shots,
                 ts.season >= {_first("lineups")} AND
                 NOT EXISTS (SELECT 1 FROM clean.lineups l WHERE l.season = ts.season AND l.season_type = ts.season_type AND l.team_id = ts.team_id) AS missing_lineups,
                 ts.season >= {_first("on_off")} AND
                 NOT EXISTS (SELECT 1 FROM clean.player_on_off o WHERE o.season = ts.season AND o.season_type = ts.season_type AND o.team_id = ts.team_id) AS missing_on_off
             FROM ts WHERE missing_shots OR missing_lineups OR missing_on_off"""),
    Check("regular_season_game_count", "warn",
          "A completed regular season has its full game count: 1230, 1189 before 2004-05 (29 teams), "
          "fewer in shortened seasons (current season will be lower: expected)",
          f"""WITH short(season, expected) AS (VALUES {", ".join(f"('{k}', {v})" for k, v in SHORT_SEASONS.items())})
             SELECT g.season, count(*) AS games,
                    any_value(coalesce(short.expected,
                              CASE WHEN g.season < '{TWENTY_NINE_TEAMS_UNTIL}' THEN 1189 ELSE 1230 END)) AS expected
             FROM clean.games g LEFT JOIN short USING (season)
             WHERE g.season_type = 'Regular Season' GROUP BY 1 HAVING games <> expected"""),
    Check("lineup_row_cap", "error",
          "No team returned exactly 2000 lineups (that would mean NBA.com truncated the list)",
          """SELECT season, season_type, team_id, count(*) AS n FROM clean.lineups
             GROUP BY ALL HAVING n >= 2000"""),

    # ---------- reference integrity ----------
    Check("players_known", "error", "Every player in a box score exists in clean.players",
          """SELECT DISTINCT player_id, player_name FROM clean.player_box
             WHERE player_id NOT IN (SELECT player_id FROM clean.players)"""),
    Check("players_have_bio", "warn", "Players who appear in our box scores have bio data (height, etc.)",
          """SELECT DISTINCT b.player_id, b.player_name FROM clean.player_box b
             JOIN clean.players p USING (player_id) WHERE NOT p.has_bio"""),
    Check("teams_have_conference", "error", "Every team has a conference and division",
          "SELECT * FROM clean.teams WHERE conference IS NULL OR division IS NULL"),
    Check("teams_unique_per_season", "error", "One name/abbreviation per team per season",
          "SELECT season, team_id, count(*) AS n FROM clean.teams GROUP BY ALL HAVING n > 1"),

    # ---------- box score totals ----------
    Check("player_sum_equals_team_box", "error",
          "Sum of player stats = team box score, per team per game",
          f"""WITH p AS (SELECT game_id, team_id, {", ".join(f"sum({c}) AS {c}" for c in STAT_COLS)}
                         FROM clean.player_box GROUP BY ALL)
              SELECT t.game_id, t.team_id, {", ".join(f"p.{c} AS p_{c}, t.{c} AS t_{c}" for c in STAT_COLS)}
              FROM clean.team_box t JOIN p USING (game_id, team_id)
              WHERE {_cmp("p", "t", STAT_COLS)}"""),
    Check("team_box_equals_official", "error",
          "Team box score = NBA.com's official game log line (points, shooting, rebounds, ...). "
          "Turnovers compare tov_total (players + team turnovers from play-by-play)",
          """WITH gl AS (SELECT DISTINCT GAME_ID AS game_id, nint(TEAM_ID) AS team_id,
                              nint(PTS) AS pts, nint(FGM) AS fgm, nint(FGA) AS fga, nint(FG3M) AS fg3m,
                              nint(FTM) AS ftm, nint(FTA) AS fta, nint(REB) AS reb, nint(AST) AS ast,
                              nint(TOV) AS tov
                         FROM raw.leaguegamelog__league_game_log)
             SELECT t.game_id, t.team_id, t.pts, gl.pts AS official_pts, t.fgm, gl.fgm AS official_fgm,
                    t.fga, gl.fga AS official_fga, t.fg3m, gl.fg3m AS official_fg3m, t.ftm, gl.ftm AS official_ftm,
                    t.reb, gl.reb AS official_reb, t.ast, gl.ast AS official_ast, t.tov, gl.tov AS official_tov
             FROM (SELECT * REPLACE (tov_total AS tov) FROM clean.team_box) t JOIN gl USING (game_id, team_id)
             WHERE """ + _cmp("t", "gl", ["pts", "fgm", "fga", "fg3m", "ftm", "fta", "reb", "ast", "tov"])),

    # ---------- play-by-play vs box ----------
    Check("pbp_final_score_equals_official", "error",
          "Final score in play-by-play = official final score",
          """WITH f AS (SELECT game_id, max(score_home) AS pbp_home, max(score_away) AS pbp_away
                        FROM clean.pbp_events GROUP BY 1)
             SELECT g.game_id, g.home_pts, f.pbp_home, g.away_pts, f.pbp_away
             FROM clean.games g JOIN f USING (game_id)
             WHERE g.home_pts IS DISTINCT FROM f.pbp_home OR g.away_pts IS DISTINCT FROM f.pbp_away"""),
    Check("pbp_shot_counts_equal_box", "error",
          "Made FG, made 3s, FGA and made free throws counted from play-by-play = team box score",
          """WITH p AS (
                 SELECT game_id, team_id,
                        count(*) FILTER (WHERE action_type = 'Made Shot')                     AS fgm,
                        count(*) FILTER (WHERE action_type IN ('Made Shot', 'Missed Shot'))   AS fga,
                        count(*) FILTER (WHERE action_type = 'Made Shot' AND shot_value = 3)  AS fg3m,
                        count(*) FILTER (WHERE action_type = 'Free Throw'
                                           AND description NOT ILIKE 'MISS %')                 AS ftm
                 FROM clean.pbp_events WHERE team_id IS NOT NULL GROUP BY ALL)
             SELECT t.game_id, t.team_id, t.fgm, p.fgm AS pbp_fgm, t.fga, p.fga AS pbp_fga,
                    t.fg3m, p.fg3m AS pbp_fg3m, t.ftm, p.ftm AS pbp_ftm
             FROM clean.team_box t JOIN p USING (game_id, team_id)
             WHERE """ + _cmp("t", "p", ["fgm", "fga", "fg3m", "ftm"])),
    Check("game_length_matches_periods", "warn",
          "Team minutes (sum of players / 5) match the number of periods in play-by-play",
          """SELECT t.game_id, t.team_id, t.player_seconds_total / 5 / 60.0 AS team_minutes, g.periods,
                    48 + (g.periods - 4) * 5 AS expected_minutes
             FROM clean.team_box t JOIN clean.games g USING (game_id)
             WHERE abs(t.player_seconds_total / 5 / 60.0 - (48 + (g.periods - 4) * 5)) > 0.5"""),

    # ---------- shots ----------
    Check("shots_equal_box", "error",
          "Shot chart FGA/FGM per player per game = box score",
          """WITH s AS (SELECT game_id, player_id, count(*) AS fga, count(*) FILTER (WHERE made) AS fgm,
                               count(*) FILTER (WHERE shot_value = 3) AS fg3a
                        FROM clean.shots GROUP BY ALL)
             SELECT b.game_id, b.player_id, b.player_name, b.fga, s.fga AS shots_fga, b.fgm, s.fgm AS shots_fgm,
                    b.fg3a, s.fg3a AS shots_fg3a
             FROM clean.player_box b
             LEFT JOIN s USING (game_id, player_id)
             WHERE (b.season, b.season_type) IN (SELECT DISTINCT season, season_type FROM clean.shots)
               AND (coalesce(b.fga, 0) <> coalesce(s.fga, 0) OR coalesce(b.fgm, 0) <> coalesce(s.fgm, 0)
                    OR coalesce(b.fg3a, 0) <> coalesce(s.fg3a, 0))"""),
    Check("shots_link_to_pbp", "warn",
          "Each shot links to a play-by-play shot event (game_id + game_event_id = action_number). "
          "Only games whose play-by-play is downloaded (game_completeness reports the rest)",
          """SELECT s.game_id, s.game_event_id, s.player_name, s.period, s.clock_seconds_remaining
             FROM clean.shots s
             LEFT JOIN clean.pbp_events p
                    ON p.game_id = s.game_id AND p.action_number = s.game_event_id
                   AND p.action_type IN ('Made Shot', 'Missed Shot')
             WHERE p.game_id IS NULL
               AND s.game_id IN (SELECT DISTINCT game_id FROM clean.pbp_events)"""),

    # ---------- lineups / on-off ----------
    Check("lineup_minutes_equal_team", "error",
          "Sum of 5-man lineup minutes = team minutes for the season (within 1%)",
          """WITH l AS (SELECT season, season_type, team_id, sum(minutes) AS lineup_min FROM clean.lineups GROUP BY ALL),
                  t AS (SELECT season, season_type, team_id, sum(player_seconds_total) / 5 / 60.0 AS team_min
                        FROM clean.team_box GROUP BY ALL)
             SELECT t.*, l.lineup_min, round(100 * (l.lineup_min - t.team_min) / t.team_min, 2) AS pct_diff
             FROM t JOIN l USING (season, season_type, team_id)
             WHERE abs(l.lineup_min - t.team_min) > 0.01 * t.team_min"""),
    # Not "on + off = team minutes": a traded player only has off-court minutes for his stint,
    # and injured/inactive roster players have off-court rows only.
    Check("on_off_on_minutes_equal_box", "error",
          "For each player, on-court minutes (on/off data) = his box-score minutes for that team (within 1 min)",
          """WITH o AS (SELECT season, season_type, team_id, player_id, any_value(player_name) AS player_name,
                               sum(minutes) FILTER (WHERE on_court) AS on_min
                        FROM clean.player_on_off GROUP BY season, season_type, team_id, player_id),
                  b AS (SELECT season, season_type, team_id, player_id, sum(seconds_played) / 60.0 AS box_min
                        FROM clean.player_box GROUP BY ALL)
             SELECT o.*, b.box_min
             FROM o LEFT JOIN b USING (season, season_type, team_id, player_id)
             WHERE abs(coalesce(o.on_min, 0) - coalesce(b.box_min, 0)) > 1"""),
    Check("on_off_minutes_within_team", "error",
          "For each player, on-court + off-court minutes <= team minutes for the season type (within 1%)",
          """WITH o AS (SELECT season, season_type, team_id, player_id, any_value(player_name) AS player_name,
                               sum(minutes) FILTER (WHERE on_court) AS on_min,
                               sum(minutes) FILTER (WHERE NOT on_court) AS off_min
                        FROM clean.player_on_off GROUP BY season, season_type, team_id, player_id),
                  t AS (SELECT season, season_type, team_id, sum(player_seconds_total) / 5 / 60.0 AS team_min
                        FROM clean.team_box GROUP BY ALL)
             SELECT o.*, t.team_min
             FROM o JOIN t USING (season, season_type, team_id)
             WHERE coalesce(o.on_min, 0) + coalesce(o.off_min, 0) > 1.01 * t.team_min"""),

    # ---------- matchups ----------
    Check("matchup_players_in_game", "error",
          "Offensive player is on off_team_id in that game's box score; defender is on the other team",
          """SELECT m.game_id, m.off_player_id, m.off_player_name, m.def_player_id, m.def_player_name, m.off_team_id
             FROM clean.matchups m
             LEFT JOIN clean.player_box o ON o.game_id = m.game_id AND o.player_id = m.off_player_id
             LEFT JOIN clean.player_box d ON d.game_id = m.game_id AND d.player_id = m.def_player_id
             WHERE o.team_id IS DISTINCT FROM m.off_team_id OR d.team_id IS NULL OR d.team_id = m.off_team_id"""),
]


def load_known_gaps(path: Path | None = None) -> list[dict]:
    path = path or KNOWN_GAPS_PATH
    if not path.exists():
        return []
    with open(path, "rb") as f:
        return tomllib.load(f).get("gap", [])


def _split_known(df, gaps: list[dict]):
    """(unexpected rows, known rows, gaps that matched nothing). A gap matches a row only if every
    column it names has exactly that value (compared as text, so 7 == 7.0 is NOT assumed)."""
    if df.empty or not gaps:
        return df, df.iloc[0:0], gaps
    known = df.index == -1  # all False
    unused = []
    for gap in gaps:
        hit = df.index != -1  # all True
        for col, value in gap["row"].items():
            hit &= df[col].astype(str).eq(str(value)).to_numpy() if col in df.columns else False
        if hit.any():
            known |= hit
        else:
            unused.append(gap)
    return df[~known], df[known], unused


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def record_known_gap(check: str, row: dict, why: str, path: Path | None = None) -> bool:
    """Append one known NBA.com gap to known_gaps.toml. False if an identical entry is already there."""
    path = path or KNOWN_GAPS_PATH
    if any(g["check"] == check and g["row"] == row for g in load_known_gaps(path)):
        return False
    fields = ", ".join(f"{k} = {_toml_value(v)}" for k, v in row.items())
    with open(path, "a", encoding="utf-8") as f:
        f.write(f'\n[[gap]]\ncheck = "{check}"\nrow = {{ {fields} }}\nwhy = {_toml_value(why)}\n')
    return True


def run_checks(con, out_dir: Path, known_gaps: list[dict] | None = None, first_seasons: dict | None = None) -> int:
    """Run every check, store results in meta.*, write offending rows to CSV. Returns # failed errors.

    known_gaps: offending rows that are NBA.com's own data gaps (see known_gaps.toml). They don't fail
    the check but are still counted and written to <check>.known.csv. None = load known_gaps.toml.
    first_seasons: first season of each optional dataset (config.toml [datasets]). None = defaults."""
    from .config import DATASET_FIRST_SEASON
    gaps = load_known_gaps() if known_gaps is None else known_gaps
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute("CREATE OR REPLACE TABLE meta.datasets (dataset VARCHAR, first_season VARCHAR)")
    con.executemany("INSERT INTO meta.datasets VALUES (?, ?)",
                    list((first_seasons or DATASET_FIRST_SEASON).items()))
    con.execute("""CREATE OR REPLACE TABLE meta.dq_results (
        check_name VARCHAR, severity VARCHAR, description VARCHAR, passed BOOLEAN,
        n_offending BIGINT, n_known BIGINT, sample_json VARCHAR, ran_at TIMESTAMP)""")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.csv"):
        old.unlink()

    ran_at = datetime.now(timezone.utc).replace(tzinfo=None)
    failed_errors = 0
    stale = []
    print(f"\n{'CHECK':<36} {'RESULT':<8} OFFENDING ROWS")
    for c in CHECKS:
        df, known, unused = _split_known(con.sql(c.sql).df(), [g for g in gaps if g["check"] == c.name])
        stale += unused
        n = len(df)
        passed = n == 0
        if not passed:
            df.to_csv(out_dir / f"{c.name}.csv", index=False)
            if c.severity == "error":
                failed_errors += 1
        if len(known):
            known.to_csv(out_dir / f"{c.name}.known.csv", index=False)
        sample = df.head(20).to_json(orient="records", date_format="iso") if n else "[]"
        con.execute("INSERT INTO meta.dq_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [c.name, c.severity, c.description, passed, n, len(known), sample, ran_at])
        label = "PASS" if passed else ("FAIL" if c.severity == "error" else "WARN")
        extra = f"  (+{len(known)} known NBA.com gap{'s' if len(known) > 1 else ''})" if len(known) else ""
        print(f"{c.name:<36} {label:<8} {'' if passed else n}{extra}")

    if failed_errors:
        print(f"\n{failed_errors} error-level check(s) FAILED.")
    for g in stale:
        print(f"Note: known gap for {g['check']} {g['row']} no longer appears. "
              f"If its season is in this database, the entry in known_gaps.toml can be removed.")
    print(f"Every offending row of every non-passing check is in {out_dir} (known gaps: *.known.csv)")
    return failed_errors


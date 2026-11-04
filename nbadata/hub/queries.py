"""Every query the hub runs. Each takes an open DuckDB connection and returns plain dicts/lists.

Rules used everywhere:
- GP counts games actually played (`played`); GS counts `is_starter`; minutes come from `seconds_played`.
- Shooting percentages come from summed makes/attempts, never from averaging per-game percentages.
- A player who played for several teams in a season gets one row per team plus a TOT row.
"""

import re
import unicodedata

SEASON_TYPES = ["Regular Season", "PlayIn", "Playoffs"]
SEASON_TYPE_LABELS = {"Regular Season": "Regular Season", "PlayIn": "Play-In", "Playoffs": "Playoffs"}

# Summed per player per team per season; per-game values are derived from these.
SUMS = {"sec": "seconds_played", "pts": "pts", "fgm": "fgm", "fga": "fga", "fg3m": "fg3m", "fg3a": "fg3a",
        "ftm": "ftm", "fta": "fta", "oreb": "oreb", "dreb": "dreb", "reb": "reb", "ast": "ast", "stl": "stl",
        "blk": "blk", "tov": "tov", "pf": "pf", "plus_minus": "plus_minus"}


def _dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def rows(con, sql: str, params=None) -> list[dict]:
    return _dicts(con.execute(sql, params or []))


def _div(a, b):
    return a / b if a is not None and b else None


def finish(r: dict) -> dict:
    """Add per-game averages and shooting percentages to a row of season totals (gp + SUMS)."""
    gp = r["gp"]
    for k in SUMS:
        r[k] = r.get(k) or 0
        r[k + "_pg"] = _div(r[k], gp)
    r["min"] = r["sec"] / 60
    r["min_pg"] = _div(r["min"], gp)
    r["fg_pct"] = _div(r["fgm"], r["fga"])
    r["fg3_pct"] = _div(r["fg3m"], r["fg3a"])
    r["ft_pct"] = _div(r["ftm"], r["fta"])
    tsa = r["fga"] + 0.44 * r["fta"]
    r["ts_pct"] = _div(r["pts"], 2 * tsa)
    return r


def add_up(rows_: list[dict], **extra) -> dict:
    total = {"gp": sum(x["gp"] for x in rows_), "gs": sum(x["gs"] for x in rows_), **extra}
    for k in SUMS:
        total[k] = sum(x[k] or 0 for x in rows_)
    return finish(total)


# --- whole-database facts -------------------------------------------------------------------------

def seasons(con) -> list[dict]:
    """Seasons in the database, newest first, with game counts per season type."""
    return rows(con, """
        SELECT season,
               count(*) FILTER (WHERE season_type = 'Regular Season') AS regular,
               count(*) FILTER (WHERE season_type = 'PlayIn') AS playin,
               count(*) FILTER (WHERE season_type = 'Playoffs') AS playoffs
        FROM clean.games GROUP BY season ORDER BY season DESC""")


def dataset_first_seasons(con) -> dict:
    return dict(con.execute("SELECT dataset, first_season FROM meta.datasets").fetchall())


def dq_status(con) -> dict:
    failing = rows(con, """SELECT check_name, severity, n_offending FROM meta.dq_results
                           WHERE NOT passed ORDER BY severity, check_name""")
    built_at, n_checks, n_known = con.execute(
        "SELECT max(ran_at), count(*), coalesce(sum(n_known), 0) FROM meta.dq_results").fetchone()
    return {"failing": failing, "built_at": built_at, "n_checks": n_checks, "n_known": n_known,
            "errors": [f for f in failing if f["severity"] == "error"],
            "warnings": [f for f in failing if f["severity"] != "error"]}


# --- search ------------------------------------------------------------------------------------------

def normalize(text: str) -> str:
    """'Nikola Jokić' -> 'nikola jokic'; "D'Angelo" -> 'dangelo'; 'Karl-Anthony' -> 'karl anthony'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"['’.]", "", text)
    return " ".join(re.split(r"[^a-z0-9]+", text)).strip()


def player_index(con) -> list[dict]:
    """Every player we know of (bio list + anyone in a box score), with how many games we have for them."""
    out = rows(con, """
        WITH g AS (SELECT player_id, any_value(player_name) AS box_name,
                          count(*) FILTER (WHERE played) AS games, min(season) AS first_season,
                          max(season) AS last_season
                   FROM clean.player_box GROUP BY player_id)
        SELECT player_id, coalesce(p.player_name, g.box_name) AS player_name, p.from_year, p.to_year,
               p.position, coalesce(g.games, 0) AS games, g.first_season, g.last_season
        FROM clean.players p FULL JOIN g USING (player_id)""")
    for r in out:
        r["norm"] = normalize(r["player_name"])
        r["squashed"] = r["norm"].replace(" ", "")
        r["words"] = r["norm"].split()
    return out


def search_players(index: list[dict], q: str, limit: int = 25) -> list[dict]:
    """Every query word must appear in the name (accents, case, hyphens and apostrophes ignored).
    Best first: exact name, name starts with the query, a word starts with it, anywhere; then most games."""
    qn = normalize(q)
    if not qn:
        return []
    tokens = [t for t in qn.split()]
    qs = qn.replace(" ", "")
    hits = []
    for r in index:
        if not all(t in r["squashed"] for t in tokens):
            continue
        if r["squashed"] == qs:
            rank = 0
        elif r["squashed"].startswith(qs):
            rank = 1
        elif any(w.startswith(tokens[0]) for w in r["words"]):
            rank = 2
        else:
            rank = 3
        hits.append((rank, -r["games"], r["player_name"], r))
    hits.sort(key=lambda h: h[:3])
    return [dict(h[3], rank=h[0]) for h in hits[:limit]]


# --- players -----------------------------------------------------------------------------------------

def player(con, player_id: int) -> dict | None:
    found = rows(con, "SELECT * FROM clean.players WHERE player_id = ?", [player_id])
    if found:
        return found[0]
    # In a box score but not in NBA.com's player list: build a minimal bio from the box score.
    found = rows(con, "SELECT player_id, any_value(player_name) AS player_name FROM clean.player_box "
                      "WHERE player_id = ? GROUP BY player_id", [player_id])
    return found[0] if found else None


def player_seasons(con, player_id: int) -> dict:
    """{season_type: {"rows": [...], "career": {...}}} for the season types the player has games in.
    Rows are per season and team (in the order he played for them), plus a TOT row after a traded season."""
    sums = ", ".join(f"sum(b.{col}) AS {k}" for k, col in SUMS.items())
    per_team = rows(con, f"""
        SELECT b.season, b.season_type, b.team_id, coalesce(any_value(t.team_abbreviation), '?') AS team,
               min(b.game_date) AS first_game, count(*) AS gp, count(*) FILTER (WHERE b.is_starter) AS gs, {sums}
        FROM clean.player_box b
        LEFT JOIN clean.teams t ON t.season = b.season AND t.team_id = b.team_id
        WHERE b.player_id = ? AND b.played
        GROUP BY b.season, b.season_type, b.team_id
        ORDER BY b.season, first_game""", [player_id])
    out = {}
    for st in SEASON_TYPES:
        mine = [finish(r) for r in per_team if r["season_type"] == st]
        if not mine:
            continue
        table = []
        for season in sorted({r["season"] for r in mine}):
            teams = [r for r in mine if r["season"] == season]
            table += teams
            if len(teams) > 1:
                table.append(add_up(teams, season=season, season_type=st, team="TOT", team_id=None, is_total=True))
        n_seasons = len({r["season"] for r in mine})
        out[st] = {"rows": table, "career": add_up(mine, season=f"Career ({n_seasons} season{'s' * (n_seasons != 1)})",
                                                  season_type=st, team="", team_id=None, is_total=True)}
    return out


def player_log_options(con, player_id: int) -> list[tuple[str, str]]:
    """(season, season_type) pairs the player has box-score rows for, newest season first."""
    return con.execute("SELECT DISTINCT season, season_type FROM clean.player_box WHERE player_id = ? "
                       "ORDER BY season DESC", [player_id]).fetchall()


GAMELOG_SORTS = {"date": "game_date", "opp": "opp", "res": "margin", "gs": "is_starter", "min": "sec",
                 "pts": "pts", "reb": "reb", "ast": "ast", "stl": "stl", "blk": "blk", "tov": "tov",
                 "fg": "fgm", "3p": "fg3m", "ft": "ftm", "pm": "plus_minus"}


def game_log(con, player_id: int, season: str, season_type: str, sort: str = "date", desc: bool = False) -> list[dict]:
    log = rows(con, """
        SELECT b.game_id, b.game_date, b.is_home, b.is_starter, b.played, b.status_comment,
               b.seconds_played AS sec, b.pts, b.reb, b.oreb, b.dreb, b.ast, b.stl, b.blk, b.tov, b.pf,
               b.fgm, b.fga, b.fg3m, b.fg3a, b.ftm, b.fta, b.plus_minus,
               coalesce(t.team_abbreviation, '?') AS team, coalesce(o.team_abbreviation, '?') AS opp,
               g.is_neutral_site, g.is_overtime, g.periods,
               CASE WHEN b.is_home THEN g.home_pts ELSE g.away_pts END AS team_pts,
               CASE WHEN b.is_home THEN g.away_pts ELSE g.home_pts END AS opp_pts,
               g.winner_team_id = b.team_id AS won
        FROM clean.player_box b
        LEFT JOIN clean.games g USING (game_id)
        LEFT JOIN clean.teams t ON t.season = b.season AND t.team_id = b.team_id
        LEFT JOIN clean.teams o ON o.season = b.season AND o.team_id = b.opponent_team_id
        WHERE b.player_id = ? AND b.season = ? AND b.season_type = ?""", [player_id, season, season_type])
    for r in log:
        r["margin"] = r["team_pts"] - r["opp_pts"] if r["team_pts"] is not None and r["opp_pts"] is not None else None
        if not r["played"]:  # DNP: no stat line to sort on
            for k in ("sec", "pts", "reb", "ast", "stl", "blk", "tov", "fgm", "fg3m", "ftm", "plus_minus"):
                r[k] = None
    return sort_rows(log, GAMELOG_SORTS.get(sort, "game_date"), desc)


def sort_rows(rows_: list[dict], key: str, desc: bool) -> list[dict]:
    """Sort by `key`, blanks always last."""
    present = [r for r in rows_ if r[key] is not None]
    blank = [r for r in rows_ if r[key] is None]
    return sorted(present, key=lambda r: r[key], reverse=desc) + blank

"""Builds a tiny fake cache (one game, BOS vs NYK) shaped like real NBA.com responses.

Every number is internally consistent, so a correct build passes every check.
Tests then break one number on purpose to prove the checks catch it.
"""

import gzip
import json

from nbadata.http import cache_key

GAME = "0022400061"
SEASON, ST = "2024-25", "Regular Season"
BOS, NYK = 1610612738, 1610612752
TEAM_META = {
    BOS: dict(teamId=BOS, teamCity="Boston", teamName="Celtics", teamTricode="BOS", teamSlug="celtics"),
    NYK: dict(teamId=NYK, teamCity="New York", teamName="Knicks", teamTricode="NYK", teamSlug="knicks"),
}
# player_id, team, first, last, starter position, fgm, fga, fg3m, fg3a, ftm, fta, reb, ast
PLAYERS = [
    (1628369, BOS, "Jayson", "Tatum", "F", 10, 20, 4, 8, 6, 6, 8, 5),
    (1627759, BOS, "Jaylen", "Brown", "G", 8, 15, 1, 5, 3, 4, 4, 3),
    (1628973, NYK, "Jalen", "Brunson", "G", 9, 18, 3, 7, 4, 4, 2, 7),
    (1626157, NYK, "Karl-Anthony", "Towns", "C", 6, 10, 1, 2, 2, 2, 12, 1),
]


def pts(p):
    _, _, _, _, _, fgm, _, fg3m, _, ftm, *_ = p
    return 2 * fgm + fg3m + ftm


def team_totals(team):
    rows = [p for p in PLAYERS if p[1] == team]
    keys = ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "ast"]
    tot = {k: sum(r[5 + i] for r in rows) for i, k in enumerate(keys)}
    tot["pts"] = sum(pts(r) for r in rows)
    return tot


def trad_stats(fgm, fga, fg3m, fg3a, ftm, fta, reb, ast, points, minutes):
    return dict(minutes=minutes, fieldGoalsMade=fgm, fieldGoalsAttempted=fga, fieldGoalsPercentage=round(fgm / fga, 3),
                threePointersMade=fg3m, threePointersAttempted=fg3a, threePointersPercentage=round(fg3m / fg3a, 3),
                freeThrowsMade=ftm, freeThrowsAttempted=fta, freeThrowsPercentage=round(ftm / fta, 3),
                reboundsOffensive=0, reboundsDefensive=reb, reboundsTotal=reb, assists=ast, steals=0, blocks=0,
                turnovers=0, foulsPersonal=0, points=points, plusMinusPoints=0.0)


def adv_stats(minutes):
    return dict(minutes=minutes, estimatedOffensiveRating=110.0, offensiveRating=111.0, estimatedDefensiveRating=105.0,
                defensiveRating=106.0, estimatedNetRating=5.0, netRating=5.0, assistPercentage=0.2, assistToTurnover=2.0,
                assistRatio=15.0, offensiveReboundPercentage=0.1, defensiveReboundPercentage=0.2, reboundPercentage=0.15,
                turnoverRatio=10.0, effectiveFieldGoalPercentage=0.55, trueShootingPercentage=0.6, usagePercentage=0.25,
                estimatedUsagePercentage=0.25, estimatedPace=100.0, pace=100.0, pacePer40=83.3, possessions=100,
                PIE=0.15)


def player_meta(p):
    pid, _, first, last, pos, *_ = p
    return dict(personId=pid, firstName=first, familyName=last, nameI=f"{first[0]}. {last}",
                playerSlug=last.lower(), position=pos, comment="", jerseyNum="0")


def box_traditional():
    def team(t):
        tot = team_totals(t)
        players = [dict(**player_meta(p), statistics=trad_stats(*p[5:13], pts(p), "48:00")) for p in PLAYERS if p[1] == t]
        stats = trad_stats(*(tot[k] for k in ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "ast", "pts"]), "240:00")
        sb = {k: v for k, v in stats.items() if k != "plusMinusPoints"}
        return dict(**TEAM_META[t], players=players, statistics=stats, starters=sb, bench=sb)
    return {"meta": {}, "boxScoreTraditional": dict(gameId=GAME, homeTeamId=BOS, awayTeamId=NYK,
                                                       homeTeam=team(BOS), awayTeam=team(NYK))}


def box_advanced():
    def team(t):
        players = [dict(**player_meta(p), statistics=adv_stats("48:00")) for p in PLAYERS if p[1] == t]
        stats = dict(adv_stats("240:00"), estimatedTeamTurnoverPercentage=0.12)
        return dict(**TEAM_META[t], players=players, statistics=stats)
    return {"meta": {}, "boxScoreAdvanced": dict(gameId=GAME, homeTeamId=BOS, awayTeamId=NYK,
                                                 homeTeam=team(BOS), awayTeam=team(NYK))}


def pbp_actions():
    """Generate shots/free throws matching the box score, with running scores."""
    actions, score = [], {BOS: 0, NYK: 0}
    n = 0

    def add(p, action_type, sub, desc, value, made, is_fg):
        nonlocal n
        n += 1
        if made:
            score[p[1]] += value
        actions.append(dict(
            actionNumber=n, clock=f"PT{11 - (n % 11):02d}M{n % 60:02d}.00S", period=1 + (n - 1) % 4,
            teamId=p[1], teamTricode=TEAM_META[p[1]]["teamTricode"], personId=p[0], playerName=p[3],
            playerNameI=p[3], xLegacy=10, yLegacy=50, shotDistance=5 if is_fg else 0,
            shotResult=("Made" if made else "Missed") if is_fg else "", isFieldGoal=1 if is_fg else 0,
            scoreHome=str(score[BOS]), scoreAway=str(score[NYK]), pointsTotal=0,
            location="h" if p[1] == BOS else "v", description=desc, actionType=action_type, subType=sub,
            videoAvailable=1, shotValue=value if is_fg else 0, actionId=n))

    for p in PLAYERS:
        _, _, _, last, _, fgm, fga, fg3m, fg3a, ftm, fta, *_ = p
        fg2m, fg2a = fgm - fg3m, fga - fg3a
        for i in range(fg3a):
            made = i < fg3m
            add(p, "Made Shot" if made else "Missed Shot", "Jump Shot", f"{last} 3PT Jump Shot", 3, made, True)
        for i in range(fg2a):
            made = i < fg2m
            add(p, "Made Shot" if made else "Missed Shot", "Layup", f"{last} Layup", 2, made, True)
        for i in range(fta):
            made = i < ftm
            add(p, "Free Throw", "Free Throw 1 of 1", ("" if made else "MISS ") + f"{last} Free Throw 1 of 1", 1, made, False)
    return actions


def pbp():
    return {"meta": {}, "game": dict(gameId=GAME, videoAvailable=1, actions=pbp_actions())}


def matchups():
    def mstats():
        return dict(matchupMinutes="5:23", matchupMinutesSort=5.38, partialPossessions=10.5,
                    percentageDefenderTotalTime=0.3, percentageOffensiveTotalTime=0.3, percentageTotalTimeBothOn=0.4,
                    switchesOn=0, playerPoints=8, teamPoints=12, matchupAssists=1, matchupPotentialAssists=2,
                    matchupTurnovers=0, matchupBlocks=0, matchupFieldGoalsMade=3, matchupFieldGoalsAttempted=6,
                    matchupFieldGoalsPercentage=0.5, matchupThreePointersMade=1, matchupThreePointersAttempted=2,
                    matchupThreePointersPercentage=0.5, helpBlocks=0, helpFieldGoalsMade=0, helpFieldGoalsAttempted=0,
                    helpFieldGoalsPercentage=0, matchupFreeThrowsMade=1, matchupFreeThrowsAttempted=1, shootingFouls=1)

    def team(t, opp):
        players = []
        for p in [p for p in PLAYERS if p[1] == t]:
            d = next(q for q in PLAYERS if q[1] == opp)
            dm = {k: v for k, v in player_meta(d).items() if k not in ("position", "comment")}
            players.append(dict(**player_meta(p), matchups=[dict(**dm, statistics=mstats())]))
        return dict(**TEAM_META[t], players=players)
    return {"meta": {}, "boxScoreMatchups": dict(gameId=GAME, homeTeam=team(BOS, NYK), awayTeam=team(NYK, BOS))}


def legacy(name, headers, rows, extra_sets=()):
    sets = [{"name": name, "headers": headers, "rowSet": rows}] + list(extra_sets)
    return {"resource": "x", "parameters": {}, "resultSets": sets}


GL_HEADERS = ["SEASON_ID", "TEAM_ID", "TEAM_ABBREVIATION", "TEAM_NAME", "GAME_ID", "GAME_DATE", "MATCHUP", "WL", "MIN",
              "FGM", "FGA", "FG_PCT", "FG3M", "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB", "REB", "AST",
              "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS", "VIDEO_AVAILABLE"]


def gamelog_rows():
    rows = []
    for t, opp, matchup in [(BOS, NYK, "BOS vs. NYK"), (NYK, BOS, "NYK @ BOS")]:
        x, o = team_totals(t), team_totals(opp)
        name = "Boston Celtics" if t == BOS else "New York Knicks"
        rows.append(["22024", t, TEAM_META[t]["teamTricode"], name, GAME, "2024-10-22", matchup,
                     "W" if x["pts"] > o["pts"] else "L", 240, x["fgm"], x["fga"], 0.5, x["fg3m"], x["fg3a"], 0.4,
                     x["ftm"], x["fta"], 0.8, 0, x["reb"], x["reb"], x["ast"], 0, 0, 0, 0, x["pts"],
                     x["pts"] - o["pts"], 1])
    return rows


def shots():
    hdr = ["GRID_TYPE", "GAME_ID", "GAME_EVENT_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_NAME", "PERIOD",
           "MINUTES_REMAINING", "SECONDS_REMAINING", "EVENT_TYPE", "ACTION_TYPE", "SHOT_TYPE", "SHOT_ZONE_BASIC",
           "SHOT_ZONE_AREA", "SHOT_ZONE_RANGE", "SHOT_DISTANCE", "LOC_X", "LOC_Y", "SHOT_ATTEMPTED_FLAG",
           "SHOT_MADE_FLAG", "GAME_DATE", "HTM", "VTM"]
    by_team = {BOS: [], NYK: []}
    for a in pbp_actions():
        if a["isFieldGoal"]:
            made = a["actionType"] == "Made Shot"
            by_team[a["teamId"]].append([
                "Shot Chart Detail", GAME, a["actionNumber"], a["personId"], a["playerName"], a["teamId"], "x",
                a["period"], 5, 30, "Made Shot" if made else "Missed Shot", a["subType"],
                "3PT Field Goal" if a["shotValue"] == 3 else "2PT Field Goal", "Restricted Area", "Center(C)",
                "Less Than 8 ft.", 5, 10, 50, 1, int(made), "20241022", "BOS", "NYK"])
    avg = {"name": "LeagueAverages", "headers": ["GRID_TYPE", "FGA"], "rowSet": []}
    return {t: legacy("Shot_Chart_Detail", hdr, rows, [avg]) for t, rows in by_team.items()}


def lineups(team, measure):
    names = [p for p in PLAYERS if p[1] == team]
    gid = "-" + "-".join(str(p[0]) for p in names) + "-"
    if measure == "Base":
        tot = team_totals(team)
        hdr = ["GROUP_SET", "GROUP_ID", "GROUP_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "GP", "W", "L", "W_PCT", "MIN",
               "FGM", "FGA", "FG_PCT", "FG3M", "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB", "REB", "AST",
               "TOV", "STL", "BLK", "BLKA", "PF", "PFD", "PTS", "PLUS_MINUS"]
        row = ["Lineups", gid, "x", team, "x", 1, 1, 0, 1.0, 48.0, tot["fgm"], tot["fga"], 0.5, tot["fg3m"], tot["fg3a"],
               0.4, tot["ftm"], tot["fta"], 0.8, 0, tot["reb"], tot["reb"], tot["ast"], 0, 0, 0, 0, 0, 0, tot["pts"], 10.0]
    else:
        hdr = ["GROUP_SET", "GROUP_ID", "GROUP_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "GP", "MIN", "OFF_RATING",
               "DEF_RATING", "NET_RATING", "AST_PCT", "AST_TO", "AST_RATIO", "OREB_PCT", "DREB_PCT", "REB_PCT",
               "TM_TOV_PCT", "EFG_PCT", "TS_PCT", "PACE", "POSS", "PIE"]
        row = ["Lineups", gid, "x", team, "x", 1, 48.0, 110, 100, 10, 0.5, 2, 15, 0.2, 0.8, 0.5, 0.1, 0.55, 0.6, 100, 100, 0.5]
    return legacy("Lineups", hdr, [row])


def on_off(team):
    hdr = ["GROUP_SET", "TEAM_ID", "TEAM_ABBREVIATION", "TEAM_NAME", "VS_PLAYER_ID", "VS_PLAYER_NAME", "COURT_STATUS",
           "GP", "W", "L", "W_PCT", "MIN", "FGM", "FGA", "FG_PCT", "FG3M", "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT",
           "OREB", "DREB", "REB", "AST", "TOV", "STL", "BLK", "BLKA", "PF", "PFD", "PTS", "PLUS_MINUS"]

    def rows(status, minutes):
        return [["x", team, "x", "x", p[0], p[3], status, 1, 1, 0, 1.0, minutes] + [0] * 21
                for p in PLAYERS if p[1] == team]
    overall = {"name": "OverallTeamPlayerOnOffDetails", "headers": ["GROUP_SET"], "rowSet": [["x"]]}
    off = {"name": "PlayersOffCourtTeamPlayerOnOffDetails", "headers": hdr, "rowSet": rows("Off", 0.0)}
    return legacy("PlayersOnCourtTeamPlayerOnOffDetails", hdr, rows("On", 48.0), [overall, off])


def players_lists():
    cap_hdr = ["PERSON_ID", "DISPLAY_LAST_COMMA_FIRST", "DISPLAY_FIRST_LAST", "ROSTERSTATUS", "FROM_YEAR", "TO_YEAR",
               "PLAYERCODE", "PLAYER_SLUG", "TEAM_ID", "TEAM_CITY", "TEAM_NAME", "TEAM_ABBREVIATION", "TEAM_CODE",
               "TEAM_SLUG", "GAMES_PLAYED_FLAG", "OTHERLEAGUE_EXPERIENCE_CH"]
    cap = [[p[0], f"{p[3]}, {p[2]}", f"{p[2]} {p[3]}", 1, "2017", "2026", "x", p[3].lower(), p[1], "x", "x", "x",
            "x", "x", "Y", "00"] for p in PLAYERS]
    pix_hdr = ["PERSON_ID", "PLAYER_LAST_NAME", "PLAYER_FIRST_NAME", "PLAYER_SLUG", "TEAM_ID", "TEAM_SLUG",
               "IS_DEFUNCT", "TEAM_CITY", "TEAM_NAME", "TEAM_ABBREVIATION", "JERSEY_NUMBER", "POSITION", "HEIGHT",
               "WEIGHT", "COLLEGE", "COUNTRY", "DRAFT_YEAR", "DRAFT_ROUND", "DRAFT_NUMBER", "ROSTER_STATUS",
               "FROM_YEAR", "TO_YEAR", "PTS", "REB", "AST", "STATS_TIMEFRAME", "SUPPLEMENTAL_STATUS"]
    pix = [[p[0], p[3], p[2], p[3].lower(), p[1], "x", 0, "x", "x", "x", "0", "F", "6-8", "210", "Duke", "USA",
            2017 if p[0] != 1628973 else None, 1, 3, 1, "2017", "2026", 20, 5, 5, "Season", None] for p in PLAYERS]
    return legacy("CommonAllPlayers", cap_hdr, cap), legacy("PlayerIndex", pix_hdr, pix)


def write_entry(cache_dir, endpoint, params, body):
    key = cache_key(endpoint, params)
    folder = cache_dir / endpoint
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / f"{key}.json.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps(body))
    (folder / f"{key}.meta.json").write_text(json.dumps(
        {"endpoint": endpoint, "params": params, "url": "https://x", "fetched_at": "2026-10-20T00:00:00+00:00"}))


def build_fake_cache(cache_dir, mutate=None):
    """mutate(endpoint, body) may edit a response before it's written (to plant errors)."""
    def w(endpoint, params, body):
        if mutate:
            mutate(endpoint, body)
        write_entry(cache_dir, endpoint, params, body)

    for st, rows in [(ST, gamelog_rows()), ("PlayIn", []), ("Playoffs", [])]:
        w("leaguegamelog", {"Season": SEASON, "SeasonType": st, "PlayerOrTeam": "T"},
          legacy("LeagueGameLog", GL_HEADERS, rows))
    cap, pix = players_lists()
    w("commonallplayers", {"Season": "2026-27"}, cap)
    w("playerindex", {"Season": "2026-27", "Historical": "1"}, pix)
    w("boxscoretraditionalv3", {"GameID": GAME}, box_traditional())
    w("boxscoreadvancedv3", {"GameID": GAME}, box_advanced())
    w("playbyplayv3", {"GameID": GAME}, pbp())
    w("boxscorematchupsv3", {"GameID": GAME}, matchups())
    for team, body in shots().items():
        w("shotchartdetail", {"TeamID": team, "Season": SEASON, "SeasonType": ST, "ContextMeasure": "FGA"}, body)
    for team in (BOS, NYK):
        for m in ("Base", "Advanced"):
            w("leaguedashlineups", {"TeamID": team, "Season": SEASON, "SeasonType": ST, "MeasureType": m},
              lineups(team, m))
        w("teamplayeronoffdetails", {"TeamID": team, "Season": SEASON, "SeasonType": ST}, on_off(team))

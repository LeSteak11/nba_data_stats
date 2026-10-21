"""Stage 0: one live call per endpoint we plan to use, to prove each works from YOUR connection.

Prints a pass/fail table and writes data/smoke_report.json, which holds dataset names,
column names and row counts only (no actual data rows), so it's safe to paste to Claude.
"""

import json
import logging
import traceback

from nba_api.stats import endpoints as ep

from .http import BlockedError, NBAClient

log = logging.getLogger(__name__)


def _summarize(endpoint_obj) -> dict:
    out = {}
    for name, ds in _named_datasets(endpoint_obj):
        df = ds.get_data_frame()
        out[name] = {"rows": int(len(df)), "columns": [str(c) for c in df.columns]}
    return out


def _named_datasets(endpoint_obj):
    """Pair each DataSet attribute with its attribute name (e.g. 'player_stats')."""
    seen = set()
    for attr, val in vars(endpoint_obj).items():
        if type(val).__name__ == "DataSet" and id(val) not in seen:
            seen.add(id(val))
            yield attr, val


def run_smoke(cfg: dict) -> int:
    client = NBAClient(cfg)
    season = cfg["seasons"]["pilot"]
    current = cfg["seasons"]["current"]
    report = {"pilot_season": season, "current_season": current, "checks": []}

    def check(label, endpoint_cls, allow_empty=False, **kwargs):
        print(f"  {label:<45}", end="", flush=True)
        entry = {"label": label, "endpoint": endpoint_cls.__name__, "params": kwargs}
        try:
            obj = client.call(endpoint_cls, **kwargs)
            entry["datasets"] = _summarize(obj)
            rows = {k: v["rows"] for k, v in entry["datasets"].items()}
            has_rows = any(r > 0 for r in rows.values())
            entry["ok"] = has_rows or allow_empty
            print("OK   " if has_rows else ("EMPTY (expected)" if allow_empty else "EMPTY"), rows)
            report["checks"].append(entry)
            return obj
        except BlockedError:
            raise
        except Exception as e:
            entry["ok"] = False
            entry["error"] = f"{type(e).__name__}: {e}"
            entry["traceback"] = traceback.format_exc(limit=3)
            print("FAIL ", entry["error"][:150])
            report["checks"].append(entry)
            return None

    print(f"\nSmoke test against NBA.com (pilot season {season}, current {current})\n")

    # 1. Game list: also gives us a real game/team/player to test the per-game endpoints with.
    gl = check("LeagueGameLog regular season (teams)", ep.LeagueGameLog,
               season=season, season_type_all_star="Regular Season", player_or_team_abbreviation="T")
    check("LeagueGameLog play-in", ep.LeagueGameLog,
          season=season, season_type_all_star="PlayIn", player_or_team_abbreviation="T")
    check("LeagueGameLog playoffs", ep.LeagueGameLog,
          season=season, season_type_all_star="Playoffs", player_or_team_abbreviation="T")
    check("LeagueGameLog current season so far", ep.LeagueGameLog,
          season=current, season_type_all_star="Regular Season", player_or_team_abbreviation="T",
          max_age_hours=6, allow_empty=True)  # empty until the first games of the season are final
    check("ScheduleLeagueV2 current season", ep.ScheduleLeagueV2, season=current, max_age_hours=24)

    if gl is None or gl.league_game_log.get_data_frame().empty:
        print("\nNo games came back from LeagueGameLog, so per-game checks are skipped.")
        return _finish(cfg, report, client)

    first = gl.league_game_log.get_data_frame().sort_values("GAME_DATE").iloc[0]
    game_id, team_id = str(first["GAME_ID"]), int(first["TEAM_ID"])
    report["sample_game"] = {"game_id": game_id, "team_id": team_id, "date": str(first["GAME_DATE"])}
    print(f"\n  Using sample game {game_id} ({first['MATCHUP']}, {first['GAME_DATE']})\n")

    # 2. Reference tables
    check("CommonAllPlayers", ep.CommonAllPlayers, season=season, is_only_current_season=0)
    check("PlayerIndex", ep.PlayerIndex, season=season)

    # 3. Per-game endpoints (these are ~4 calls x every game in the full build)
    check("BoxScoreTraditionalV3", ep.BoxScoreTraditionalV3, game_id=game_id)
    check("BoxScoreAdvancedV3", ep.BoxScoreAdvancedV3, game_id=game_id)
    check("PlayByPlayV3", ep.PlayByPlayV3, game_id=game_id)
    check("BoxScoreMatchupsV3", ep.BoxScoreMatchupsV3, game_id=game_id)

    # 4. Season-level endpoints (pulled once per team/season, not per game)
    check("ShotChartDetail (one team, one game)", ep.ShotChartDetail,
          team_id=team_id, player_id=0, season_nullable=season,
          season_type_all_star="Regular Season", context_measure_simple="FGA", game_id_nullable=game_id)
    check("LeagueDashLineups 5-man Base", ep.LeagueDashLineups,
          season=season, season_type_all_star="Regular Season", group_quantity=5,
          measure_type_detailed_defense="Base", per_mode_detailed="Totals")
    check("LeagueDashLineups 5-man Advanced", ep.LeagueDashLineups,
          season=season, season_type_all_star="Regular Season", group_quantity=5,
          measure_type_detailed_defense="Advanced", per_mode_detailed="Totals")
    check("TeamPlayerOnOffDetails (one team)", ep.TeamPlayerOnOffDetails,
          team_id=team_id, season=season, season_type_all_star="Regular Season", per_mode_detailed="Totals")

    return _finish(cfg, report, client)


def _finish(cfg, report, client) -> int:
    failed = [c["label"] for c in report["checks"] if not c["ok"]]
    report["live_requests"] = client.live_requests
    report["cache_hits"] = client.cache_hits
    out = cfg["paths"]["data_dir"] / "smoke_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"\n{len(report['checks']) - len(failed)}/{len(report['checks'])} checks returned data.")
    print(f"Live requests: {client.live_requests}, cache hits: {client.cache_hits}")
    if failed:
        print("Not OK:", ", ".join(failed))
    print(f"Report written to {out}")
    return 1 if failed else 0

"""Download everything for a season into the cache. No database work happens here."""

import gzip
import json
import logging
import time
from datetime import date, timedelta

from nba_api.stats import endpoints as ep

from ..http import BlockedError, FetchError, NBAClient

log = logging.getLogger(__name__)

# Per-game endpoints, and the dataset in each that must be non-empty for the response to count.
PER_GAME = [
    (ep.BoxScoreTraditionalV3, "player_stats"),
    (ep.BoxScoreAdvancedV3, "player_stats"),
    (ep.PlayByPlayV3, "play_by_play"),
    (ep.BoxScoreMatchupsV3, "player_stats"),
]

REFRESH_HOURS_CURRENT = 20  # current-season aggregates change daily; re-download if older than this


class Failures:
    """Collects requests that failed so they're reported at the end instead of silently skipped."""

    def __init__(self):
        self.items = []

    def add(self, what: str, error: Exception):
        log.warning("FAILED %s: %s", what, error)
        self.items.append({"what": what, "error": str(error)})


def run_pull(cfg: dict, seasons: list[str], recent_days: int = 3, client=None, ui=None) -> int:
    from ..ui import make_ui
    client = client or NBAClient(cfg)
    ui = ui or make_ui(client)
    failures = Failures()
    started = time.monotonic()
    code = 0
    try:
        with ui:
            pull_reference(client, cfg, failures, ui)
            for season in seasons:
                pull_season(client, cfg, season, failures, recent_days, ui)
    except BlockedError as e:
        stop = f"STOPPED: {e}"
        code = 2
    except KeyboardInterrupt:
        stop = "STOPPED by you (Ctrl+C). Everything downloaded so far is saved; re-run to continue."
        code = 130
    else:
        stop = None
        code = 1 if failures.items else 0

    _print_summary(cfg, client, seasons, failures, time.monotonic() - started, stop)
    _write_failures(cfg, failures)
    return code


def _print_summary(cfg, client, seasons, failures, seconds, stop):
    from rich.console import Console
    from ..ui import summary_table
    console = Console()
    if stop:
        console.print(f"\n[bold red]{stop}[/]")
    rows = [
        ("Seasons", ", ".join(seasons)),
        ("Time", f"{seconds / 60:.0f} min"),
        ("Downloaded", f"{client.live_requests:,} requests"),
        ("Already cached", f"{client.cache_hits:,} requests"),
        ("Retries", str(client.retries)),
        ("Failed", f"[red]{len(failures.items)}[/]" if failures.items else "[green]0[/]"),
    ]
    console.print()
    console.print(summary_table(rows, "Pull summary"))
    console.print("\n[bold]Download progress, all seasons[/]")
    print_season_progress(cfg, console)


def _write_failures(cfg, failures):
    out = cfg["paths"]["log_dir"] / "pull_failures.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(failures.items, indent=2), encoding="utf-8")
    if failures.items:
        print(f"{len(failures.items)} requests FAILED. They are listed in {out}")
        print("Re-running the same command retries only those (everything else is cached).")
    else:
        print("No failed requests.")


def pull_reference(client, cfg, failures, ui):
    """Player list + bios. These are all-time lists, so one call each, refreshed daily."""
    current = cfg["seasons"]["current"]
    calls = [
        (ep.CommonAllPlayers, dict(season=current, is_only_current_season=0), "common_all_players"),
        # Historical=1 asks for every player ever, not just one season (the smoke test's 138 rows)
        (ep.PlayerIndex, dict(season=current, historical_nullable="1"), "player_index"),
    ]
    ui.phase("reference", len(calls))
    for cls, kwargs, needs in calls:
        try:
            client.call(cls, max_age_hours=24, require_rows=needs, **kwargs)
        except FetchError as e:
            failures.add(f"{cls.__name__} {kwargs}", e)
        ui.advance("reference")


def pull_season(client, cfg, season, failures, recent_days, ui):
    is_current = season == cfg["seasons"]["current"]
    max_age = REFRESH_HOURS_CURRENT if is_current else None
    ui.season(season, is_current)

    # 1. Which games exist (completed games only; LeagueGameLog never lists unplayed games)
    games = {}   # game_id -> game_date
    teams = {}   # season_type -> set of team ids
    season_types = cfg["seasons"]["season_types"]
    ui.phase("gamelogs", len(season_types))
    for st in season_types:
        try:
            gl = client.call(ep.LeagueGameLog, season=season, season_type_all_star=st,
                             player_or_team_abbreviation="T", max_age_hours=6 if is_current else None)
        except FetchError as e:
            failures.add(f"LeagueGameLog {season} {st}", e)
            ui.advance("gamelogs")
            continue
        df = gl.league_game_log.get_data_frame()
        ui.advance("gamelogs")
        ui.note(f"{st:<15} {df['GAME_ID'].nunique():>5} games")
        if df.empty:
            continue
        games.update(dict(zip(df["GAME_ID"].astype(str), df["GAME_DATE"].astype(str))))
        teams[st] = sorted(df["TEAM_ID"].astype(int).unique())

    # 2. Team-level season aggregates (fast: a few hundred calls)
    team_calls = [(st, t) for st, ts in teams.items() for t in ts]
    ui.phase("team", len(team_calls) * 4, f"{len(team_calls)} team/season-type combos x 4 calls")
    for st, team_id in team_calls:
        common = dict(season_type_all_star=st, max_age_hours=max_age)
        jobs = [
            (ep.ShotChartDetail, dict(team_id=team_id, player_id=0, season_nullable=season,
                                      context_measure_simple="FGA"), "shot_chart_detail"),
            # Per team, because the league-wide query is silently capped at 2000 rows
            (ep.LeagueDashLineups, dict(season=season, team_id_nullable=team_id, group_quantity=5,
                                        measure_type_detailed_defense="Base", per_mode_detailed="Totals"), "lineups"),
            (ep.LeagueDashLineups, dict(season=season, team_id_nullable=team_id, group_quantity=5,
                                        measure_type_detailed_defense="Advanced", per_mode_detailed="Totals"), "lineups"),
            (ep.TeamPlayerOnOffDetails, dict(team_id=team_id, season=season, per_mode_detailed="Totals"),
             "players_on_court_team_player_on_off_details"),
        ]
        for cls, kwargs, needs in jobs:
            try:
                client.call(cls, require_rows=needs, **common, **kwargs)
            except FetchError as e:
                failures.add(f"{cls.__name__} {season} {st} team {team_id}", e)
            ui.advance("team")

    # 3. Per-game data (the slow part: 4 calls per game)
    game_ids = sorted(games)
    recent_cutoff = (date.today() - timedelta(days=recent_days)).isoformat()
    ui.phase("games", len(game_ids), f"{len(game_ids)} games x {len(PER_GAME)} calls")
    for gid in game_ids:
        # Stat corrections land a few days after a game; re-download recent current-season games.
        refresh = is_current and games[gid] >= recent_cutoff
        for cls, needs in PER_GAME:
            try:
                client.call(cls, game_id=gid, refresh=refresh, require_rows=needs)
            except FetchError as e:
                failures.add(f"{cls.__name__} {gid}", e)
        ui.advance("games")


# ---------- progress per season (reads only the cache, safe during a pull) ----------

PER_GAME_FOLDERS = ["boxscoretraditionalv3", "boxscoreadvancedv3", "playbyplayv3", "boxscorematchupsv3"]


def season_progress(cfg) -> dict:
    """season -> (games fully downloaded, games known). Seasons with no game list yet -> (0, None)."""
    from ..config import all_seasons
    cache = cfg["paths"]["cache_dir"]

    def game_ids(endpoint):
        return {json.loads(m.read_text(encoding="utf-8"))["params"].get("GameID")
                for m in (cache / endpoint).glob("*.meta.json")}

    done_sets = [game_ids(e) for e in PER_GAME_FOLDERS]
    done = set.intersection(*done_sets) if done_sets else set()

    season_games = {}
    for meta in (cache / "leaguegamelog").glob("*.meta.json"):
        season = json.loads(meta.read_text(encoding="utf-8"))["params"]["Season"]
        with gzip.open(meta.with_name(meta.name.replace(".meta.json", ".json.gz")), "rt", encoding="utf-8") as f:
            rs = json.load(f)["resultSets"][0]
        i = rs["headers"].index("GAME_ID")
        season_games.setdefault(season, set()).update(str(r[i]) for r in rs["rowSet"])

    out = {}
    for season in all_seasons(cfg):
        games = season_games.get(season)
        out[season] = (len(games & done), len(games)) if games else (0, None)
    return out


def print_season_progress(cfg, console=None):
    from rich.console import Console
    from rich.table import Table
    console = console or Console()
    t = Table(show_header=True, header_style="bold")
    t.add_column("Season", no_wrap=True)
    t.add_column("Games", justify="right", no_wrap=True)
    t.add_column("", no_wrap=True)
    t.add_column("Status")
    for season, (n, total) in season_progress(cfg).items():
        current = " (current)" if season == cfg["seasons"]["current"] else ""
        if total is None:
            t.add_row(season + current, "-", "", "[dim]not started[/]")
            continue
        filled = int(20 * n / total)
        bar = "█" * filled + "░" * (20 - filled)
        status = "[green]complete[/]" if n == total else f"[yellow]{n / total:.0%}[/]"
        t.add_row(season + current, f"{n:,}/{total:,}", bar, status)
    console.print(t)

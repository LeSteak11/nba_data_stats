"""Download everything for a season into the cache. No database work happens here."""

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


def run_pull(cfg: dict, seasons: list[str], recent_days: int = 3, client=None) -> int:
    client = client or NBAClient(cfg)
    failures = Failures()
    started = time.monotonic()
    try:
        pull_reference(client, cfg, failures)
        for season in seasons:
            pull_season(client, cfg, season, failures, recent_days)
    except BlockedError as e:
        print(f"\nSTOPPED: {e}")
        _write_failures(cfg, failures)
        return 2

    mins = (time.monotonic() - started) / 60
    print(f"\nPull finished in {mins:.0f} min. Live requests: {client.live_requests}, cache hits: {client.cache_hits}")
    _write_failures(cfg, failures)
    return 1 if failures.items else 0


def _write_failures(cfg, failures):
    out = cfg["paths"]["log_dir"] / "pull_failures.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(failures.items, indent=2), encoding="utf-8")
    if failures.items:
        print(f"{len(failures.items)} requests FAILED. They are listed in {out}")
        print("Re-running the same command retries only those (everything else is cached).")
    else:
        print("No failed requests.")


def pull_reference(client, cfg, failures):
    """Player list + bios. These are all-time lists, so one call each, refreshed daily."""
    current = cfg["seasons"]["current"]
    print("Reference: players")
    for cls, kwargs, needs in [
        (ep.CommonAllPlayers, dict(season=current, is_only_current_season=0), "common_all_players"),
        # Historical=1 asks for every player ever, not just one season (the smoke test's 138 rows)
        (ep.PlayerIndex, dict(season=current, historical_nullable="1"), "player_index"),
    ]:
        try:
            client.call(cls, max_age_hours=24, require_rows=needs, **kwargs)
        except FetchError as e:
            failures.add(f"{cls.__name__} {kwargs}", e)


def pull_season(client, cfg, season, failures, recent_days):
    is_current = season == cfg["seasons"]["current"]
    max_age = REFRESH_HOURS_CURRENT if is_current else None
    print(f"\n=== {season}{' (current season)' if is_current else ''} ===")

    # 1. Which games exist (completed games only; LeagueGameLog never lists unplayed games)
    games = {}   # game_id -> game_date
    teams = {}   # season_type -> set of team ids
    for st in cfg["seasons"]["season_types"]:
        try:
            gl = client.call(ep.LeagueGameLog, season=season, season_type_all_star=st,
                             player_or_team_abbreviation="T", max_age_hours=6 if is_current else None)
        except FetchError as e:
            failures.add(f"LeagueGameLog {season} {st}", e)
            continue
        df = gl.league_game_log.get_data_frame()
        print(f"  {st:<15} {df['GAME_ID'].nunique():>5} games")
        if df.empty:
            continue
        games.update(dict(zip(df["GAME_ID"].astype(str), df["GAME_DATE"].astype(str))))
        teams[st] = sorted(df["TEAM_ID"].astype(int).unique())

    # 2. Team-level season aggregates (fast: a few hundred calls)
    team_calls = [(st, t) for st, ts in teams.items() for t in ts]
    print(f"  Team-level data: {len(team_calls)} team/season-type combos x 4 calls")
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

    # 3. Per-game data (the slow part: 4 calls per game)
    game_ids = sorted(games)
    recent_cutoff = (date.today() - timedelta(days=recent_days)).isoformat()
    print(f"  Per-game data: {len(game_ids)} games x {len(PER_GAME)} calls")
    t0, live0 = time.monotonic(), client.live_requests
    for i, gid in enumerate(game_ids, 1):
        # Stat corrections land a few days after a game; re-download recent current-season games.
        refresh = is_current and games[gid] >= recent_cutoff
        for cls, needs in PER_GAME:
            try:
                client.call(cls, game_id=gid, refresh=refresh, require_rows=needs)
            except FetchError as e:
                failures.add(f"{cls.__name__} {gid}", e)
        if i % 25 == 0 or i == len(game_ids):
            _progress(i, len(game_ids), t0, client.live_requests - live0)


def _progress(done, total, t0, live):
    elapsed = time.monotonic() - t0
    rate = elapsed / done
    eta = rate * (total - done) / 60
    print(f"    {done:>5}/{total} games  ({live} live requests so far, ~{eta:.0f} min left)", flush=True)

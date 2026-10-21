"""Command line entry point. Run from the project folder:  python -m nbadata <command>"""

import argparse
import logging
import sys

from .config import load_config


def _setup_logging(cfg):
    log_dir = cfg["paths"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_dir / "nbadata.log", encoding="utf-8"), logging.StreamHandler()],
    )


def cmd_smoke(cfg, args):
    from .smoke import run_smoke
    return run_smoke(cfg)


def _seasons(cfg, args):
    from .config import all_seasons
    if args.all:
        return all_seasons(cfg)
    if not args.season:
        raise SystemExit("Say which seasons: --season 2024-25 (repeatable) or --all")
    known = all_seasons(cfg)
    for s in args.season:
        if s not in known:
            raise SystemExit(f"Unknown season {s}. Seasons in config.toml: {', '.join(known)}")
    return args.season


def cmd_pull(cfg, args):
    from .pull import run_pull
    return run_pull(cfg, _seasons(cfg, args))


def cmd_build_db(cfg, args):
    from .db import build_db
    return build_db(cfg)


def cmd_build(cfg, args):
    code = cmd_pull(cfg, args)
    if code == 2:  # stopped because NBA.com is blocking; don't build on top of that
        return code
    if code == 1:
        print("\nSome requests failed (see above). Building anyway: the checks will show what's missing.\n")
    return cmd_build_db(cfg, args) or code


def cmd_check(cfg, args):
    from .db import recheck
    return recheck(cfg)


def cmd_query(cfg, args):
    from .db import run_query
    return run_query(cfg, args.sql, args.rows)


def cmd_progress(cfg, args):
    """How many games are fully downloaded, per season. Reads only the cache, safe during a run."""
    import gzip
    import json
    cache = cfg["paths"]["cache_dir"]

    def game_ids(endpoint):
        return {json.loads(m.read_text(encoding="utf-8"))["params"].get("GameID")
                for m in (cache / endpoint).glob("*.meta.json")}

    per_game = ["boxscoretraditionalv3", "boxscoreadvancedv3", "playbyplayv3", "boxscorematchupsv3"]
    done_sets = [game_ids(e) for e in per_game]
    done = set.intersection(*done_sets) if done_sets else set()

    season_games = {}
    for meta in (cache / "leaguegamelog").glob("*.meta.json"):
        season = json.loads(meta.read_text(encoding="utf-8"))["params"]["Season"]
        with gzip.open(meta.with_name(meta.name.replace(".meta.json", ".json.gz")), "rt", encoding="utf-8") as f:
            rs = json.load(f)["resultSets"][0]
        i = rs["headers"].index("GAME_ID")
        season_games.setdefault(season, set()).update(str(r[i]) for r in rs["rowSet"])

    if not season_games:
        print("No game lists cached yet.")
        return 0
    for season, games in sorted(season_games.items()):
        if not games:
            continue
        n = len(games & done)
        bar = "#" * int(30 * n / len(games))
        print(f"  {season}  [{bar:<30}] {n:>5}/{len(games)} games  {n / len(games):.0%}")
    return 0


def cmd_cache_info(cfg, args):
    cache = cfg["paths"]["cache_dir"]
    if not cache.exists():
        print(f"No cache yet at {cache}")
        return 0
    total_files = total_bytes = 0
    for d in sorted(p for p in cache.iterdir() if p.is_dir()):
        files = list(d.glob("*.json.gz"))
        size = sum(f.stat().st_size for f in files)
        total_files += len(files)
        total_bytes += size
        print(f"  {d.name:<30} {len(files):>7} responses  {size / 1e6:>9.1f} MB")
    print(f"  {'TOTAL':<30} {total_files:>7} responses  {total_bytes / 1e6:>9.1f} MB  ({cache})")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nbadata", description="NBA data foundation")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("smoke", help="Stage 0: test every NBA.com endpoint we depend on")
    sub.add_parser("cache-info", help="Show what's in the local cache")
    sub.add_parser("progress", help="How many games are fully downloaded, per season (safe during a pull)")
    for name, help_ in [("pull", "Download data for the given seasons into the cache (no database work)"),
                        ("build", "pull + build-db in one go")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--season", action="append", help="e.g. 2024-25 (repeat for several)")
        p.add_argument("--all", action="store_true", help="every season in config.toml")
    sub.add_parser("build-db", help="Rebuild the database from the cache, then run checks (no downloading)")
    sub.add_parser("check", help="Re-run the data-quality checks on the existing database")
    q = sub.add_parser("query", help='Run SQL and print the result: query "SELECT ..."')
    q.add_argument("sql")
    q.add_argument("--rows", type=int, default=40, help="max rows to print")
    args = parser.parse_args(argv)

    cfg = load_config()
    _setup_logging(cfg)
    handlers = {"smoke": cmd_smoke, "cache-info": cmd_cache_info, "pull": cmd_pull, "build": cmd_build,
                "build-db": cmd_build_db, "check": cmd_check, "query": cmd_query, "progress": cmd_progress}
    from .http import BlockedError
    try:
        return handlers[args.command](cfg, args)
    except BlockedError as e:
        print(f"\nSTOPPED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())

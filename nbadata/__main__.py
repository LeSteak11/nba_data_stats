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
    from .awake import keep_awake
    from .pull import run_pull
    with keep_awake():
        return run_pull(cfg, _seasons(cfg, args))


def cmd_build_db(cfg, args):
    from .awake import keep_awake
    from .db import build_db
    with keep_awake():
        return build_db(cfg)


def cmd_next(cfg, args):
    """Pull + build the next season that isn't downloaded yet (newest first)."""
    from .pull import next_season
    season = next_season(cfg)
    if season is None:
        print("Every completed season is downloaded. The current season is kept up to date with "
              "`python -m nbadata update` (Stage 4, coming).")
        return 0
    print(f"Next season: {season}")
    args.season, args.all = [season], False
    return cmd_build(cfg, args)


def cmd_build(cfg, args):
    from .awake import keep_awake
    from .ui import print_finish
    seasons = _seasons(cfg, args)
    with keep_awake():
        code, build_code = _pull_then_build(cfg, args)
    print_finish(cfg, seasons, code, build_code)
    return build_code or code


def _pull_then_build(cfg, args):
    code = cmd_pull(cfg, args)
    if code in (2, 130):  # stopped (NBA.com blocking us, or Ctrl+C): don't build on top of that
        return code, None
    if code == 1:
        print("\nSome requests failed (see above). Building anyway: the checks will show what's missing.")
    print("\nBuilding the database now (a few minutes). Don't close this window yet.\n")
    return code, cmd_build_db(cfg, args)


def cmd_check(cfg, args):
    from .db import recheck
    return recheck(cfg)


def cmd_query(cfg, args):
    from .db import run_query
    return run_query(cfg, args.sql, args.rows)


def cmd_progress(cfg, args):
    """How many games are fully downloaded, per season. Reads only the cache, safe during a run."""
    from .pull import print_season_progress
    print_season_progress(cfg)
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


def _utf8_when_redirected():
    """Box-drawing characters in tables crash a cp1252 pipe/file on Windows. A real terminal is fine."""
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(argv=None):
    _utf8_when_redirected()
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
    sub.add_parser("next", help="Pull + build the next season that isn't downloaded yet, newest first")
    sub.add_parser("build-db", help="Rebuild the database from the cache, then run checks (no downloading)")
    sub.add_parser("check", help="Re-run the data-quality checks on the existing database")
    q = sub.add_parser("query", help='Run SQL and print the result: query "SELECT ..."')
    q.add_argument("sql")
    q.add_argument("--rows", type=int, default=40, help="max rows to print")
    args = parser.parse_args(argv)

    cfg = load_config()
    _setup_logging(cfg)
    handlers = {"smoke": cmd_smoke, "cache-info": cmd_cache_info, "pull": cmd_pull, "build": cmd_build,
                "build-db": cmd_build_db, "next": cmd_next, "check": cmd_check, "query": cmd_query, "progress": cmd_progress}
    from .http import BlockedError
    try:
        return handlers[args.command](cfg, args)
    except BlockedError as e:
        print(f"\nSTOPPED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""Build the DuckDB database from the cache: raw -> clean -> (analysis, Stage 3) -> checks.

The database is always rebuilt from scratch into a temporary file, then swapped in at the end.
So a failed build never leaves you with a half-built database: the previous one stays put.

On Windows the swap fails while any program has nba.duckdb open (even read-only, e.g. the hub serving a
page), so it waits up to a minute for the file to be free instead of giving up straight away.
"""

import os
import time
from pathlib import Path

import duckdb

from . import raw_load
from .checks import run_checks
from .config import dataset_first_seasons

CLEAN_DIR = Path(__file__).parent / "clean"
WAIT_FOR_FILE_SECONDS = 60


def _patiently(action, retry_on, what, wait_s, step_s=0.5, sleep=time.sleep):
    """Run action(); while it raises one of `retry_on`, retry every step_s for up to wait_s, then re-raise."""
    deadline = time.monotonic() + wait_s
    told = False
    while True:
        try:
            return action()
        except retry_on:
            if time.monotonic() >= deadline:
                raise
            if not told:
                print(f"{what} is open in another program (the hub?). Waiting up to {wait_s:.0f} s for it...")
                told = True
            sleep(step_s)


def replace_patiently(src: Path, dst: Path, wait_s: float = WAIT_FOR_FILE_SECONDS, **kw) -> None:
    _patiently(lambda: os.replace(src, dst), PermissionError, dst.name, wait_s, **kw)


def connect_patiently(path: Path, wait_s: float = WAIT_FOR_FILE_SECONDS, **kw):
    """Read-write open; DuckDB refuses it while another process has the file open."""
    return _patiently(lambda: duckdb.connect(str(path)), duckdb.IOException, path.name, wait_s, **kw)


def db_path(cfg) -> Path:
    return cfg["paths"]["data_dir"] / "nba.duckdb"


def build_db(cfg) -> int:
    final = db_path(cfg)
    tmp = final.with_name("nba.building.duckdb")
    for p in (tmp, tmp.with_name(tmp.name + ".wal")):
        p.unlink(missing_ok=True)

    t0 = time.monotonic()
    con = duckdb.connect(str(tmp))
    try:
        print("Loading raw layer from cache...")
        raw_load.load_all(con, cfg["paths"]["cache_dir"])

        print("\nBuilding clean layer...")
        for sql_file in sorted(CLEAN_DIR.glob("*.sql")):
            try:
                con.execute(sql_file.read_text(encoding="utf-8"))
            except duckdb.Error as e:
                raise RuntimeError(f"{sql_file.name} failed: {e}") from e
            table = sql_file.stem.split("_", 1)[1]
            if table != "helpers":
                n = con.sql(f"SELECT count(*) FROM clean.{table}").fetchone()[0]
                print(f"  clean.{table:<16} {n:>10,} rows")

        failed = run_checks(con, cfg["paths"]["data_dir"] / "dq", first_seasons=dataset_first_seasons(cfg))
    finally:
        con.close()

    try:
        replace_patiently(tmp, final)
    except PermissionError:
        print(f"\nCould not replace {final}: another program has it open (a SQL tool, another terminal?).")
        print(f"Close it and re-run. The new build is saved as {tmp}")
        return 3
    print(f"\nDatabase ready: {final}  ({(time.monotonic() - t0) / 60:.1f} min)")
    return 1 if failed else 0


def run_query(cfg, sql: str, max_rows: int = 40) -> int:
    path = db_path(cfg)
    if not path.exists():
        print(f"No database yet at {path}. Run the build first.")
        return 1
    with duckdb.connect(str(path), read_only=True) as con:
        con.sql(sql).show(max_rows=max_rows, max_width=200)
    return 0


def recheck(cfg) -> int:
    with connect_patiently(db_path(cfg)) as con:
        return 1 if run_checks(con, cfg["paths"]["data_dir"] / "dq", first_seasons=dataset_first_seasons(cfg)) else 0

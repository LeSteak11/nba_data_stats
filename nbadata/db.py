"""Build the DuckDB database from the cache: raw -> clean -> (analysis, Stage 3) -> checks.

The database is always rebuilt from scratch into a temporary file, then swapped in at the end.
So a failed build never leaves you with a half-built database: the previous one stays put.
"""

import os
import time
from pathlib import Path

import duckdb

from . import raw_load
from .checks import run_checks
from .config import dataset_first_seasons

CLEAN_DIR = Path(__file__).parent / "clean"


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
        os.replace(tmp, final)
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
    with duckdb.connect(str(db_path(cfg))) as con:
        return 1 if run_checks(con, cfg["paths"]["data_dir"] / "dq", first_seasons=dataset_first_seasons(cfg)) else 0

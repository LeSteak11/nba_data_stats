"""How the hub reads nba.duckdb without getting in the way of a season run.

- Every page opens the file read-only and closes it straight after. Between page loads the hub holds no
  handle on it, so `build-db` can swap a new database in (on Windows that fails while anyone has it open).
- If the file can't be opened (`check` holds the write lock), retry for a moment, then give up with
  DatabaseBusy, which the app turns into a "being updated, refresh in a moment" page.
- Small things worth remembering between pages (season list, search names) are cached and recomputed
  whenever the file changes, so a new season shows up on the next page load.
"""

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import duckdb


class DatabaseMissing(Exception):
    pass


class DatabaseBusy(Exception):
    pass


class Database:
    def __init__(self, path: Path, wait_s: float = 2.0, step_s: float = 0.25):
        self.path = Path(path)
        self.wait_s = wait_s
        self.step_s = step_s
        self._cache: dict = {}
        self._lock = threading.Lock()

    def version(self):
        """Changes whenever build-db swaps a new file in."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size, st.st_ino

    @contextmanager
    def connect(self):
        if not self.path.exists():
            raise DatabaseMissing(str(self.path))
        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                con = duckdb.connect(str(self.path), read_only=True)
                break
            except duckdb.Error as e:
                if time.monotonic() >= deadline:
                    raise DatabaseBusy(str(e)) from e
                time.sleep(self.step_s)
        try:
            yield con
        finally:
            con.close()

    def cached(self, key: str, con, compute):
        """compute(con), remembered until the database file changes."""
        version = self.version()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] == version:
                return hit[1]
        value = compute(con)
        with self._lock:
            self._cache[key] = (version, value)
        return value


def available(first_seasons: dict, dataset: str, season: str) -> bool:
    """Does the database have this optional dataset for this season? (meta.datasets; season strings sort by year.)
    Datasets not listed there (box scores, play-by-play, shots) exist for every season."""
    first = first_seasons.get(dataset)
    return first is None or season >= first

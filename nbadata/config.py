"""Load config.toml into a plain dict and resolve paths."""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"

# First season NBA.com has each optional dataset (config.toml [datasets] can override).
DATASET_FIRST_SEASON = {"lineups": "2007-08", "on_off": "2007-08", "matchups": "2017-18"}


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    data_dir = (path.parent / cfg["paths"]["data_dir"]).resolve()
    cfg["paths"]["data_dir"] = data_dir
    cfg["paths"]["cache_dir"] = data_dir / "cache"
    cfg["paths"]["log_dir"] = data_dir / "logs"
    s = cfg["seasons"]
    if "completed" not in s:
        s["completed"] = seasons_between(s["oldest"], s["current"])[:-1]
    return cfg


def seasons_between(first: str, last: str) -> list[str]:
    """'2000-01', '2002-03' -> ['2000-01', '2001-02', '2002-03']"""
    return [f"{y}-{str(y + 1)[2:]}" for y in range(int(first[:4]), int(last[:4]) + 1)]


def all_seasons(cfg: dict) -> list[str]:
    return cfg["seasons"]["completed"] + [cfg["seasons"]["current"]]


def has_dataset(cfg: dict, dataset: str, season: str) -> bool:
    """Does NBA.com have this optional dataset for this season? (Season strings sort by year.)"""
    return season >= {**DATASET_FIRST_SEASON, **cfg.get("datasets", {})}[dataset]


def dataset_first_seasons(cfg: dict) -> dict:
    return {**DATASET_FIRST_SEASON, **cfg.get("datasets", {})}

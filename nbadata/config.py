"""Load config.toml into a plain dict and resolve paths."""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    data_dir = (path.parent / cfg["paths"]["data_dir"]).resolve()
    cfg["paths"]["data_dir"] = data_dir
    cfg["paths"]["cache_dir"] = data_dir / "cache"
    cfg["paths"]["log_dir"] = data_dir / "logs"
    return cfg


def all_seasons(cfg: dict) -> list[str]:
    return cfg["seasons"]["completed"] + [cfg["seasons"]["current"]]

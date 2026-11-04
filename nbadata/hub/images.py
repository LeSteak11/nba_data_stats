"""Headshots and logos you add yourself, under data/images/ (gitignored):

    data/images/players/<player_id>.png
    data/images/teams/<team_id>.png
    data/images/teams/<team_id>_<season>.png    (optional: used for that season only)

Any of .png, .jpg, .jpeg, .webp, .svg. No file -> the app shows a placeholder.
"""

from pathlib import Path

EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")


def find_image(images_dir: Path, kind: str, item_id: int, season: str | None = None) -> Path | None:
    folder = Path(images_dir) / {"player": "players", "team": "teams"}[kind]
    names = ([f"{item_id}_{season}"] if season else []) + [str(item_id)]
    for name in names:
        for ext in EXTENSIONS:
            p = folder / (name + ext)
            if p.is_file():
                return p
    return None

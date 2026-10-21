"""Known NBA.com gaps: listed rows stop failing a check but are still reported."""

import pandas as pd

from nbadata.checks import _split_known, load_known_gaps


def test_known_gap_matches_only_an_exact_row():
    df = pd.DataFrame({"game_id": ["0022300465", "0022300465", "0022300078"],
                       "player_id": [202684, 202684, 1628991], "fga": [7, 8, 13], "flag": [True, True, False]})
    gaps = [
        {"check": "x", "row": {"game_id": "0022300465", "player_id": 202684, "fga": 7, "flag": True}},
        {"check": "x", "row": {"game_id": "0099999999"}},  # matches nothing -> reported as stale
    ]
    unexpected, known, stale = _split_known(df, gaps)
    assert list(known["fga"]) == [7]
    assert list(unexpected["fga"]) == [8, 13]  # same player/game but different numbers = a real failure
    assert stale == [gaps[1]]


def test_known_gaps_file_is_valid():
    gaps = load_known_gaps()
    assert gaps and all({"check", "row", "why"} <= set(g) for g in gaps)

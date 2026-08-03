"""Tests for the FFC adapter's normalization rules.

Deliberately offline. These lock in the two decisions that look like
over-engineering and would otherwise get simplified away: the PK/DEF position
remap, and stdev=0 becoming NaN rather than a real measurement.
"""

import numpy as np

from adapters.ffc_adapter import normalize_players, snap_team_count

# A miniature version of the API's "players" list, using real shapes seen live.
RAW = [
    {"player_id": 5672, "name": "Jahmyr Gibbs", "position": "RB", "team": "DET",
     "adp": 1.7, "adp_formatted": "1.02", "times_drafted": 567,
     "high": 1, "low": 6, "stdev": 0.8, "bye": 6},
    {"player_id": 6791, "name": "Zachariah Branch", "position": "WR", "team": "ATL",
     "adp": 177.0, "adp_formatted": "15.09", "times_drafted": 5,
     "high": 177, "low": 177, "stdev": 0.0, "bye": 11},
    {"player_id": 1111, "name": "Some Kicker", "position": "PK", "team": "KC",
     "adp": 150.0, "adp_formatted": "13.06", "times_drafted": 40,
     "high": 120, "low": 180, "stdev": 12.0, "bye": 10},
    {"player_id": 2222, "name": "NY Giants Defense", "position": "DEF", "team": "NYG",
     "adp": 181.3, "adp_formatted": "16.01", "times_drafted": 36,
     "high": 160, "low": 190, "stdev": 20.6, "bye": 11},
]


def test_positions_are_canonicalized():
    # FFC says PK/DEF; the rest of the app says K/DST. Without this remap
    # kickers and defenses silently vanish from the sim pool.
    positions = set(normalize_players(RAW)["position"])
    assert positions == {"RB", "WR", "K", "DST"}


def test_zero_stdev_becomes_nan():
    # Branch was taken 5 times at exactly pick 177. That is "unmeasurable",
    # not "no spread" -- as 0 he would be deterministic in the simulator.
    df = normalize_players(RAW).set_index("name")
    assert np.isnan(df.loc["Zachariah Branch", "stdev"])
    assert df.loc["Jahmyr Gibbs", "stdev"] == 0.8


def test_sorted_by_adp_and_formatted_column_dropped():
    df = normalize_players(RAW)
    assert list(df["adp"]) == sorted(df["adp"])
    assert "adp_formatted" not in df.columns


def test_snap_team_count():
    assert snap_team_count(12) == 12
    assert snap_team_count(13) == 12   # nearest accepted value
    assert snap_team_count(16) == 14   # 16 would be an HTTP 400

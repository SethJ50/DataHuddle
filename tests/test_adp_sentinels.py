"""Tests for ADP sentinel handling at the vendor boundary.

Sleeper writes 999 for players it has no ADP for. That is not a draft position,
but nothing about it looks wrong until it reaches blend_adp and averages with the
other platforms. Measured before the guard existed: 71 players ended up with a
blended ADP roughly 409 picks too deep, and every one was consequently pushed
past the simulation's pool cap and dropped from the model entirely.
"""

import numpy as np
import pandas as pd
import pytest

from adapters.adp_source_adapter import (
    MAX_PLAUSIBLE_ADP, EspnAdpAdapter, SleeperAdpAdapter, YahooAdpAdapter,
    _drop_sentinels,
)
from draft_model.table import blend_adp
from scoring import ScoringFormat


class StubRepo:
    def __init__(self, frame):
        self._frame = frame

    def read(self):
        return self._frame


def test_sentinel_becomes_nan():
    values = pd.Series([1.5, 120.0, 248.8, 999.0, 9999.0])
    cleaned = _drop_sentinels(values)

    assert cleaned.iloc[:3].tolist() == [1.5, 120.0, 248.8]
    assert cleaned.iloc[3:].isna().all()


def test_real_deep_adp_survives():
    # The threshold must be generous enough that no genuine ADP is clipped. Even
    # a 20-team, 25-round draft is only 500 picks.
    assert _drop_sentinels(pd.Series([499.0])).iloc[0] == pytest.approx(499.0)
    assert MAX_PLAUSIBLE_ADP >= 500


def test_sleeper_adapter_strips_sentinels():
    frame = pd.DataFrame({
        "name": ["Real Player", "Unranked Player"],
        "team": ["AAA", "BBB"],
        "position": ["WR", "TE"],
        "half_ppr_adp": [45.0, 999.0],
        "full_ppr_adp": [44.0, 999.0],
    })
    loaded = SleeperAdpAdapter(StubRepo(frame)).load(ScoringFormat.FULL_PPR)

    assert loaded.loc[0, "adp"] == pytest.approx(44.0)
    assert np.isnan(loaded.loc[1, "adp"])


def test_yahoo_adapter_strips_sentinels():
    frame = pd.DataFrame({
        "name": ["Real Player", "Unranked Player"],
        "team": ["AAA", "BBB"],
        "position": ["WR", "TE"],
        "adp": [45.0, 999.0],
    })
    loaded = YahooAdpAdapter(StubRepo(frame)).load(ScoringFormat.FULL_PPR)

    assert loaded.loc[0, "adp"] == pytest.approx(45.0)
    assert np.isnan(loaded.loc[1, "adp"])


def test_blend_ignores_a_source_that_has_no_opinion():
    # THE POINT OF ALL OF THIS. A player two platforms rank around 170 must blend
    # to about 170 -- not be dragged toward 400 because a third source wrote 999.
    espn = pd.Series({"player": 169.3})
    yahoo = pd.Series({"player": 125.4})
    sleeper = _drop_sentinels(pd.Series({"player": 999.0}))

    blended = blend_adp(
        {"espn": espn.dropna(), "yahoo": yahoo.dropna(), "sleeper": sleeper.dropna()},
        {"espn": 0.25, "yahoo": 0.5, "sleeper": 0.25},
    )

    # Weights renormalize over the two sources that actually rank him.
    expected = (0.25 * 169.3 + 0.5 * 125.4) / 0.75
    assert blended["player"] == pytest.approx(expected)
    assert blended["player"] < 200      # nowhere near the ~355 the sentinel caused

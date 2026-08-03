"""
A set of adapters that converts each source of ADP data into a common shape,
so services/adp_comparison_service.py can treat each source (ESPN, Sleeper, Yahoo)
identically regardless of underlying data quirks.
"""

from typing import Protocol
import numpy as np
import pandas as pd
from scoring import ScoringFormat

MAX_PLAUSIBLE_ADP = 500
"""Anything at or beyond this is a SENTINEL, not a draft position.

Sleeper writes 999 for players it has no ADP for -- 73 of them in the 2026 pool.
Left as a number, that value flows straight into blend_adp and averages with the
other platforms: Pat Freiermuth reads ESPN 169 / Yahoo 125 / Sleeper 999, which
blends to about 355. Measured before this guard existed, 71 players had a blended
ADP roughly 409 picks too deep, and every one of them was consequently pushed
past the simulation's pool cap and dropped from the model entirely.

The threshold is deliberately generous. Even a 20-team, 25-round draft is only
500 picks, so no real ADP can reach it, while 999 and 9999 style sentinels are
caught. Converting them to NaN makes blend_adp skip that source for that player
and renormalize over the platforms that actually have him -- which is exactly
what "this source doesn't rank him" should mean.
"""


def _drop_sentinels(adp: pd.Series) -> pd.Series:
    """Replace implausible ADP values with NaN so they read as 'missing'."""
    numeric = pd.to_numeric(adp, errors="coerce")
    return numeric.where(numeric < MAX_PLAUSIBLE_ADP, np.nan)


def _load_projection_adp(collection_repo, fmt: ScoringFormat):
    """
        Shared helper for ESPN / Sleeper since both data collections
        already have half_ppr_adp and full_ppr_columns.

        Returns: DataFrame with columns: name, team, position, adp
    """

    df = collection_repo.read()
    adp_col  = "half_ppr_adp" if fmt == ScoringFormat.HALF_PPR else "full_ppr_adp"

    return pd.DataFrame({
        "name": df["name"],
        "team": df["team"],
        "position": df["position"],
        "adp": _drop_sentinels(df[adp_col]),
    })

class EspnAdpAdapter:
    def __init__(self, collection_repo):
        self._collection_repo = collection_repo

    def load(self, fmt: ScoringFormat):
        """
        Purpose: Loads ESPN's ADP for the requested scoring format.
        Parameters: fmt (ScoringFormat) — HALF_PPR or FULL_PPR.
        Returns: pd.DataFrame with columns name, team, position, adp.
        """
        return _load_projection_adp(self._collection_repo, fmt)

class SleeperAdpAdapter:
    def __init__(self, collection_repo):
        self._collection_repo = collection_repo

    def load(self, fmt: ScoringFormat) -> pd.DataFrame:
        """
        Purpose: Loads Sleeper's ADP for the requested scoring format.
        Parameters: fmt (ScoringFormat) — HALF_PPR or FULL_PPR.
        Returns: pd.DataFrame with columns name, team, position, adp.
        Notes: Sleeper tracks genuinely different ADP per format, unlike
            ESPN (whose half/full-PPR ADP columns hold the same value).
        """
        return _load_projection_adp(self._collection_repo, fmt)

class YahooAdpAdapter:
    def __init__(self, collection_repo):
        self._collection_repo = collection_repo

    def load(self, fmt: ScoringFormat) -> pd.DataFrame:
        """
        Purpose: Loads Yahoo's ADP. `fmt` is accepted only so this class
            matches the same shape as the other two adapters — Yahoo has no
            per-format ADP split, so the value returned is the same either
            way.
        Parameters: fmt (ScoringFormat) — accepted but unused.
        Returns: pd.DataFrame with columns name, team, position, adp.
        """
        df = self._collection_repo.read()

        return pd.DataFrame({
            "name": df["name"],
            "team": df["team"],
            "position": df["position"],
            "adp": _drop_sentinels(df["adp"]),
        })
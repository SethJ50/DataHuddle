"""
A set of adapters that converts each source of ADP data into a common shape,
so services/adp_comparison_service.py can treat each source (ESPN, Sleeper, Yahoo)
identically regardless of underlying data quirks.
"""

from typing import Protocol
import pandas as pd
from scoring import ScoringFormat

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
        "adp": df[adp_col],
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
            "adp": df["adp"],
        })
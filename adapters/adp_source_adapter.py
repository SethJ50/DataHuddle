"""
A set of adapters that converts each source of ADP data into a common shape,
so services/adp_comparison_service.py can treat each source (ESPN, Sleeper, Yahoo)
identically regardless of underlying data quirks.

"ADP" is average draft position: across many real drafts, the average pick
number at which a player was taken. A lower number means he goes earlier.
"""

from typing import Protocol
import numpy as np
import pandas as pd
from scoring import ScoringFormat

MAX_PLAUSIBLE_ADP = 500
"""Anything at or beyond this is a SENTINEL, not a draft position.

A sentinel is a fake value a data source uses to mean "I have nothing here".

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
    """Replace impossibly large ADP values with NaN so they read as 'missing'.

    Every adapter below runs its ADP column through this, so a source's private
    way of saying "no data" (999, 9999) is turned into pandas' standard
    "missing" marker before it can reach any calculation.

    Steps:
        1. Convert the column to numbers. `errors="coerce"` turns anything that
           is not a number, such as a stray "-", into NaN rather than raising.
        2. Keep values below MAX_PLAUSIBLE_ADP as they are, and replace
           everything at or above it with NaN.

    Args:
        adp: A column of ADP values, one per player. May contain text or
            sentinel values; it does not have to be numeric already.

    Returns:
        pd.Series: The same column, now numeric, with sentinels and unparseable
            values replaced by NaN. Downstream averaging skips NaN entries
            automatically, which is the whole point.
    """
    numeric = pd.to_numeric(adp, errors="coerce")
    return numeric.where(numeric < MAX_PLAUSIBLE_ADP, np.nan)


def _load_projection_adp(collection_repo, fmt: ScoringFormat):
    """Read ADP from a collection that stores one column per scoring format.

    ESPN and Sleeper are stored the same way: each row already carries both a
    half-PPR and a full-PPR ADP value, so picking the right source is just
    picking the right column. Both adapters share this helper rather than
    repeating the logic.

    Steps:
        1. Call `.read()` on the repository to pull the whole collection into a
           DataFrame.
        2. Choose which ADP column to read based on the requested scoring
           format.
        3. Build the canonical four-column table, running the chosen ADP column
           through `_drop_sentinels` above so "no data" values become NaN.

    Args:
        collection_repo: An object with a `.read()` method returning the stored
            rows for one platform.
        fmt: Which scoring format to read. HALF_PPR selects the `half_ppr_adp`
            column; anything else selects `full_ppr_adp`.

    Returns:
        pd.DataFrame: One row per player with columns `name`, `team`,
            `position`, and `adp`. This four-column shape is the common
            contract every ADP adapter in this file returns.

    Raises:
        KeyError: If the collection is missing one of the expected columns.
    """

    # One row per player. Relevant columns: name, team, position, and both
    # half_ppr_adp and full_ppr_adp, only one of which is used per call.
    df = collection_repo.read()
    adp_col  = "half_ppr_adp" if fmt == ScoringFormat.HALF_PPR else "full_ppr_adp"

    return pd.DataFrame({
        "name": df["name"],
        "team": df["team"],
        "position": df["position"],
        "adp": _drop_sentinels(df[adp_col]),
    })

class EspnAdpAdapter:
    """Provides ESPN's ADP in the app's common four-column shape.

    One of three interchangeable adapters. The comparison service holds a list
    of these and calls `load` on each without caring which platform it is
    talking to.
    """

    def __init__(self, collection_repo):
        """Remember where ESPN's stored rows can be read from.

        Steps:
            1. Save the repository on the instance. Nothing is read yet; the
               database is only touched when `load` is called.

        Args:
            collection_repo: An object with a `.read()` method returning ESPN's
                stored rows as a DataFrame.
        """
        self._collection_repo = collection_repo

    def load(self, fmt: ScoringFormat):
        """Load ESPN's ADP for the requested scoring format.

        Called by the ADP comparison service when it gathers one table per
        platform to compare side by side.

        Steps:
            1. Hand the stored repository and the requested format to
               `_load_projection_adp` above, which reads the rows, picks the
               matching ADP column, and strips sentinel values.

        Args:
            fmt: The scoring format to load, HALF_PPR or FULL_PPR.

        Returns:
            pd.DataFrame: One row per player with columns `name`, `team`,
                `position`, and `adp`, where `adp` is NaN for any player ESPN
                does not rank.

        Note:
            ESPN stores the same number in its half-PPR and full-PPR columns, so
            in practice the format argument does not change the result here. It
            is still accepted so all three adapters share one signature.
        """
        return _load_projection_adp(self._collection_repo, fmt)

class SleeperAdpAdapter:
    """Provides Sleeper's ADP in the app's common four-column shape.

    Interchangeable with the ESPN and Yahoo adapters. Sleeper is the source
    that motivated `_drop_sentinels`, since it writes 999 rather than leaving a
    blank for players it has no data on.
    """

    def __init__(self, collection_repo):
        """Remember where Sleeper's stored rows can be read from.

        Steps:
            1. Save the repository on the instance for `load` to use later.

        Args:
            collection_repo: An object with a `.read()` method returning
                Sleeper's stored rows as a DataFrame.
        """
        self._collection_repo = collection_repo

    def load(self, fmt: ScoringFormat) -> pd.DataFrame:
        """Load Sleeper's ADP for the requested scoring format.

        Steps:
            1. Hand the stored repository and the requested format to
               `_load_projection_adp` above, which reads the rows, picks the
               matching ADP column, and strips sentinel values.

        Args:
            fmt: The scoring format to load, HALF_PPR or FULL_PPR. Unlike the
                other two platforms, this genuinely changes the numbers.

        Returns:
            pd.DataFrame: One row per player with columns `name`, `team`,
                `position`, and `adp`, where `adp` is NaN for the roughly 73
                players Sleeper marks with its 999 sentinel.

        Note:
            Sleeper tracks genuinely different ADP per format, unlike ESPN,
            whose half-PPR and full-PPR columns hold the same value.
        """
        return _load_projection_adp(self._collection_repo, fmt)

class YahooAdpAdapter:
    """Provides Yahoo's ADP in the app's common four-column shape.

    Interchangeable with the ESPN and Sleeper adapters, but implemented
    separately because Yahoo stores a single ADP column rather than one per
    scoring format.
    """

    def __init__(self, collection_repo):
        """Remember where Yahoo's stored rows can be read from.

        Steps:
            1. Save the repository on the instance for `load` to use later.

        Args:
            collection_repo: An object with a `.read()` method returning
                Yahoo's stored rows as a DataFrame.
        """
        self._collection_repo = collection_repo

    def load(self, fmt: ScoringFormat) -> pd.DataFrame:
        """Load Yahoo's ADP, which is the same regardless of scoring format.

        Yahoo publishes one ADP number rather than splitting it by scoring
        format, so this cannot reuse `_load_projection_adp`. The `fmt` argument
        is still accepted so that the comparison service can call every adapter
        the same way.

        Steps:
            1. Call `.read()` on the repository to pull Yahoo's rows into a
               DataFrame.
            2. Build the canonical four-column table, running the single `adp`
               column through `_drop_sentinels` above.

        Args:
            fmt: Accepted for consistency with the other adapters but unused.
                Passing HALF_PPR or FULL_PPR gives an identical result.

        Returns:
            pd.DataFrame: One row per player with columns `name`, `team`,
                `position`, and `adp`.

        Raises:
            KeyError: If Yahoo's stored rows are missing one of those columns.
        """
        # One row per player, with a single `adp` column rather than one per
        # scoring format, plus name, team, and position.
        df = self._collection_repo.read()

        return pd.DataFrame({
            "name": df["name"],
            "team": df["team"],
            "position": df["position"],
            "adp": _drop_sentinels(df["adp"]),
        })

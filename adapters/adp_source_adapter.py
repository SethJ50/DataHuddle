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
from registry import parse_positions
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
                `position`, `adp`, and `yahoo_rank`. The first four are the
                common contract every ADP adapter returns; `yahoo_rank` is an
                extra that only this source can supply.

        Raises:
            KeyError: If Yahoo's stored rows are missing one of those columns.

        Note:
            `yahoo_rank` is Yahoo's own BOARD ORDER -- the ranked list it shows a
            drafter in-app -- which is a genuinely different thing from ADP.
            Measured on the 2026 pull it correlates with Yahoo's ADP at 0.90 but
            orders 210 of 223 players differently, so it carries real
            information about who Yahoo is pushing rather than who got taken.

            Yahoo is the ONLY source that publishes one. ESPN and Sleeper store
            no rank at all, and their projections cannot stand in: sorting
            Sleeper's players by projected points puts eleven quarterbacks in
            the top fifteen, because raw points ignore positional scarcity.

            It is passed through RAW and deliberately not cleaned here. The
            values are sparse rather than a dense 1..N list -- they run to 2473
            across 1,175 players -- so anything using them has to dense-rank
            within its own pool first. `table.rank_to_pick_scale` does that.

            Carrying an extra column is safe: AdpComparisonService selects the
            columns it wants by name, so the comparison page is unaffected.
        """
        # One row per player, with a single `adp` column rather than one per
        # scoring format, plus name, team, position, and Yahoo's board rank.
        df = self._collection_repo.read()

        return pd.DataFrame({
            "name": df["name"],
            "team": df["team"],
            "position": df["position"],
            "adp": _drop_sentinels(df["adp"]),
            # Optional extra, not part of the four-column contract. Missing it
            # degrades to "no board data" rather than breaking the ADP path,
            # which every other consumer of this adapter depends on.
            "yahoo_rank": (pd.to_numeric(df["yahoo_rank"], errors="coerce")
                           if "yahoo_rank" in df.columns else np.nan),
        })


class EspnBoardRankAdapter:
    """Provides ESPN's in-draft board order -- the ranked list it shows a drafter.

    The counterpart to Yahoo's `yahoo_rank`, but it arrives as its own collection
    rather than riding along with the ADP, because ESPN publishes no rank in its
    projections export. It is scraped from the live draft board with
    scripts/espn_board_rankings_console.js.

    THE RANK IS THE ROW ORDER. ESPN prints no rank number in that table, so the
    scraper numbers the rows as it walks them. That makes the file only as good
    as the sort it was captured under -- see docs/UPDATING_DATA.md for the two
    ways to get it silently wrong.
    """

    def __init__(self, collection_repo):
        """Remember where ESPN's stored board rows can be read from.

        Steps:
            1. Save the repository on the instance. Nothing is read yet; the
               database is only touched when `load` is called.

        Args:
            collection_repo: An object with a `.read()` method returning the
                stored board rows as a DataFrame.
        """
        self._collection_repo = collection_repo

    def load(self) -> pd.DataFrame:
        """Read ESPN's board and rename its columns to the app's vocabulary.

        Steps:
            1. Call `.read()` on the repository to pull the collection into a
               DataFrame.
            2. Return an empty table with the right column names if nothing is
               stored, so callers can use those columns unconditionally.
            3. Build the canonical table, converting the rank with
               `errors="coerce"` so a malformed value becomes NaN rather than
               raising.
            4. Normalize each position with `parse_positions` from registry.py,
               taking the FIRST recognized one -- ESPN writes multi-position
               players as "WR, CB", and it also spells defenses "D/ST".
            5. Drop rows with no rank, then sort by it so the top of the board
               comes first.

        Returns:
            pd.DataFrame: One row per player, sorted by rank, with columns
                `name`, `team`, `position`, and `espn_rank` (1 being the best).

        Raises:
            KeyError: If the stored rows are missing one of the expected
                columns, which would mean the scraper's output changed.

        Note:
            Covers roughly 1,000 players including kickers and defenses -- far
            deeper than the ~250 the simulation pool needs. The extra rows cost
            nothing: they simply never match a player in the pool.
        """
        # One row per player: espn_rank, name, team, position.
        df = self._collection_repo.read()

        if df.empty:
            return pd.DataFrame(columns=["name", "team", "position", "espn_rank"])

        def first_position(raw):
            """ESPN writes "WR, CB" for multi-position players; keep the first."""
            found = parse_positions(str(raw))
            return found[0].value if found else ""

        out = pd.DataFrame({
            "name": df["name"].astype(str).str.strip(),
            "team": df["team"].astype(str).str.strip().str.upper(),
            "position": df["position"].map(first_position),
            "espn_rank": pd.to_numeric(df["espn_rank"], errors="coerce"),
        })

        out = out.dropna(subset=["espn_rank"])
        return out.sort_values("espn_rank").reset_index(drop=True)

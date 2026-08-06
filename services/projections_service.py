"""Computes fantasy point projections from adapters' raw-stat output.

This is the one place "what will this player score" is answered — the
scoring math itself lives in scoring.py (imported here, not duplicated).
Results are resolved to canonical_id and scoped to RosterService's UDK
roster, so "our own projections" and "who's in the app" never drift apart.

THREE ANALYSTS, ONE NUMBER
--------------------------
Fantasy Footballers publish separate projections from Andy, Mike and Jason. The
app's default projection is their average, because averaging independent
forecasts reliably beats picking one of them.

The more interesting output is their DISAGREEMENT. Where the three split on a
player, that is a measure of forecasting uncertainty completely independent of
ADP spread — a player everyone projects the same way but the market can't price
is a very different bet from one the analysts themselves can't agree on.
"""

import numpy as np
import pandas as pd

import scoring


class ProjectionsService:
    """Turns raw projected stats into fantasy points for the app's players.

    The one place the question "what will this player score?" is answered. It
    takes the analysts' raw stat lines, applies the scoring rules, matches every
    player to a canonical id, and narrows the result to the players the app
    considers in scope.

    Public methods are `get_own_projections`, `disagreement`, and `unresolved`;
    the underscore-prefixed ones are internal steps of the first.
    """

    def __init__(self, ffb_adapter, identity_repo, roster_service):
        """Store the three collaborators this service needs.

        All three are handed in rather than built here, so tests can supply
        fakes and the app can share one cached copy of each.

        Steps:
            1. Save the projections adapter, the identity repository, and the
               roster service on the instance. Nothing is loaded yet.

        Args:
            ffb_adapter: An `FfbProjectionsAdapter` providing the analysts' raw
                stat projections.
            identity_repo: A `PlayerIdentityRepo` used to turn the analysts'
                player names into canonical ids.
            roster_service: Supplies `canonical_ids()`, the set of players the
                app considers in scope.
        """
        self._ffb_adapter = ffb_adapter
        self._identity_repo = identity_repo
        self._roster_service = roster_service

    @property
    def analysts(self) -> list:
        """List which analysts can be selected for a single-analyst projection.

        Used to populate the analyst picker in the UI, so it only ever offers
        analysts that actually have data behind them.

        Steps:
            1. Pass through to the adapter's own `analysts` property.

        Returns:
            list: The analyst names available, such as
                `["andy", "mike", "jason"]`.
        """
        return self._ffb_adapter.analysts

    def get_own_projections(self, analyst: str = None) -> pd.DataFrame:
        """Get our own season-long and per-game fantasy point projections.

        The main entry point of this service. Ask for one analyst to see his
        numbers alone, or leave the argument out to get the blend of all three,
        which is the app's default projection.

        Steps:
            1. If a single analyst was named, load his raw stats from the
               adapter, attach canonical ids with `_resolve` below, and convert
               the stats to points with `_score` below. That is the whole job.
            2. Otherwise load every analyst's stats at once, resolve and score
               them the same way, then collapse the per-analyst rows into one row
               per player with `_blend` below.

        Args:
            analyst: A single analyst's name for his numbers alone, or None, the
                default, for the blend of all three.

        Returns:
            pd.DataFrame — one row per player, with canonical_id, the raw stat
            columns, and for each scoring format:
                fantasy_points_{fmt}_season
                fantasy_points_{fmt}_per_game
            The blend additionally carries, per format:
                fantasy_points_{fmt}_season_low / _high / _spread
            plus `n_analysts`, how many actually rated that player.

            Only players resolved to a canonical_id AND present in
            RosterService's roster are included, so this stays consistent with
            the rest of the app's player universe.

        Note:
            Scoring is a LINEAR function of the stat line, so averaging the three
            analysts' points gives exactly the same answer as averaging their
            stats and scoring once. Points are averaged because that also yields
            the high/low/spread for free.

            The average is over whoever actually rated each player, not over
            three — the analysts cover slightly different pools (Andy has 267
            flex players to Mike's and Jason's 265), and treating a missing
            projection as zero would bury deep players.
        """
        if analyst is not None:
            return self._score(self._resolve(self._ffb_adapter.load(analyst)))

        return self._blend(self._score(self._resolve(self._ffb_adapter.load_all())))

    def _resolve(self, df: pd.DataFrame) -> pd.DataFrame:
        """Attach canonical player IDs and drop anyone outside the app's roster.

        Two filters in one step. Players whose names cannot be matched to a
        canonical id are dropped because nothing downstream could join them, and
        players outside the UDK roster are dropped so this service's player set
        matches the rest of the app's.

        Steps:
            1. Ask `resolve_many_with_fallback` on the identity repository to
               turn the analysts' names into canonical ids, passing positions too
               so two players sharing a name can be told apart.
            2. Attach those ids as a new column and drop rows where resolution
               failed.
            3. Keep only the players whose id appears in the roster service's
               set of in-scope players.

        Args:
            df: Adapter output, needing at least `name` and `position` columns
                alongside the raw stat columns.

        Returns:
            pd.DataFrame: The same columns plus `canonical_id`, with
                unresolvable players and players outside the UDK roster removed.
                Can be noticeably shorter than the input.

        Note:
            FFB's names are matched the same way every other source is: the
            curated player_id_map first, then an exact name+position match with
            suffix/accent normalization (see PlayerIdentityRepo).
        """
        canonical_id = self._identity_repo.resolve_many_with_fallback(
            "ffb", df["name"], df["position"]
        )
        df = df.assign(canonical_id=canonical_id).dropna(subset=["canonical_id"])
        return df[df["canonical_id"].isin(self._roster_service.canonical_ids())]

    def _score(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add season and per-game fantasy points for every scoring format.

        Turns raw stat lines into the points those stats are worth. The formula
        itself lives in scoring.py rather than here, so ingestion scripts and the
        app cannot drift apart on what a touchdown is worth.

        Steps:
            1. Copy the table so the caller's version is not modified.
            2. Pull out the stat columns scoring.py expects, named by
               `scoring.STAT_KEYS`.
            3. Call `scoring.fantasy_points_all_formats`, which returns season
               point totals for each format in one pass.
            4. For each format, store the season total and then call
               `scoring.per_game` to divide it across the season's games.

        Args:
            df: Resolved projections carrying every column named in
                `scoring.STAT_KEYS`, such as `passing_yards` and `receptions`.

        Returns:
            pd.DataFrame: The input's columns plus two per scoring format:
                `fantasy_points_{fmt}_season` and
                `fantasy_points_{fmt}_per_game`.

        Raises:
            KeyError: If any stat column named in `scoring.STAT_KEYS` is
                missing.
        """
        df = df.copy()
        stats = {key: df[key] for key in scoring.STAT_KEYS}
        for fmt, season_points in scoring.fantasy_points_all_formats(stats).items():
            df[f"fantasy_points_{fmt.value}_season"] = season_points
            df[f"fantasy_points_{fmt.value}_per_game"] = scoring.per_game(season_points)
        return df

    def _blend(self, scored: pd.DataFrame) -> pd.DataFrame:
        """Collapse the per-analyst rows into a single row per player.

        Averaging the three analysts gives the app's default projection, and the
        same pass captures how far apart they were, which is a useful uncertainty
        signal in its own right.

        Steps:
            1. Work out which columns hold each format's season points, since
               those are the ones that get a low/high/spread.
            2. Choose which columns to average: every numeric one except the
               identity columns. See the inline comment for why `bye_week` must
               be excluded despite being a number.
            3. Group by player and average those numeric columns.
            4. Count how many rows each player had, which is how many analysts
               rated him, and merge that in as `n_analysts`.
            5. For each season-points column, take the minimum and maximum across
               analysts and attach them as `_low` and `_high`, then subtract to
               get `_spread`.
            6. Take one copy of the identity columns per player and merge them
               back on, since they were held out of the averaging.

        Args:
            scored: Long-format data, one row per player per analyst, already
                run through `_score` above.

        Returns:
            pd.DataFrame: One row per canonical id, carrying the identity
                columns, the mean of every numeric column, `_low`, `_high`, and
                `_spread` on each format's season points, and `n_analysts`.

        Note:
            `spread` is max-minus-min across analysts, not a standard deviation.
            With three observations a standard deviation is barely meaningful,
            whereas the range is exactly the question being asked: how far apart
            are they on this player.
        """
        season_columns = [f"fantasy_points_{fmt.value}_season"
                          for fmt in scoring.ScoringFormat]

        # bye_week is numeric but is an IDENTITY column, not a measurement -- it
        # is identical across analysts, and averaging it would both be
        # meaningless and collide with the label frame on merge (producing
        # bye_week_x / bye_week_y and silently breaking every consumer).
        label_columns = ["canonical_id", "name", "team", "position", "bye_week"]
        numeric = [c for c in scored.select_dtypes(include=[np.number]).columns
                   if c not in label_columns]
        blended = scored.groupby("canonical_id", as_index=False)[numeric].mean()

        counts = scored.groupby("canonical_id", as_index=False).size()
        blended = blended.merge(counts.rename(columns={"size": "n_analysts"}),
                                on="canonical_id")

        for column in season_columns:
            stats = scored.groupby("canonical_id")[column].agg(["min", "max"])
            blended[f"{column}_low"] = blended["canonical_id"].map(stats["min"])
            blended[f"{column}_high"] = blended["canonical_id"].map(stats["max"])
            blended[f"{column}_spread"] = (
                blended[f"{column}_high"] - blended[f"{column}_low"]
            )

        # Identity columns are excluded from the aggregation above; take the
        # first occurrence of each, which is identical across analysts.
        labels = scored[label_columns].drop_duplicates(subset="canonical_id")

        return labels.merge(blended, on="canonical_id")

    def disagreement(self, fmt) -> pd.DataFrame:
        """Rank players by how much the three analysts disagree about them.

        A wide spread means the forecasters themselves are unsure, which is a
        different kind of risk from a player the market cannot price. This is the
        only place in the app that surfaces it.

        Steps:
            1. Work out which column holds season points for the requested
               format.
            2. Call `get_own_projections` above with no analyst, which produces
               the blend along with its low, high, and spread columns.
            3. Keep only players rated by at least two analysts, and narrow to
               the columns worth displaying.
            4. Sort by spread, widest disagreement first, and renumber the rows.

        Args:
            fmt: Which scoring format to measure disagreement in, since the
                analysts can differ more in one format than another.

        Returns:
            pd.DataFrame: Columns `canonical_id`, `name`, `position`, the blended
                season points, its `_low`, `_high`, and `_spread`, and
                `n_analysts` — sorted with the widest disagreement first.
                Players rated by fewer than two analysts are excluded, since a
                spread of zero across one opinion means nothing.

        Note:
            A per-player uncertainty measure independent of ADP spread. Two
            players with identical projections and identical ADP can sit at
            opposite ends of this, and that difference is invisible everywhere
            else in the app.
        """
        column = f"fantasy_points_{fmt.value}_season"
        blended = self.get_own_projections()

        frame = blended[blended["n_analysts"] >= 2][[
            "canonical_id", "name", "position", column,
            f"{column}_low", f"{column}_high", f"{column}_spread", "n_analysts",
        ]]
        return frame.sort_values(f"{column}_spread", ascending=False).reset_index(drop=True)

    def unresolved(self, analyst: str = None) -> list:
        """List projection names that could not be matched to any known player.

        A diagnostic. Every name here is a player silently missing from the
        projections, and the fix is normally to add a row to the hand-curated
        player_id_map.

        Steps:
            1. Load the raw projections, for one analyst if named or for all of
               them otherwise.
            2. Hand the names and positions to `unresolved_with_fallback` on the
               identity repository, which reports whatever neither the curated
               mapping nor the exact name match could resolve.

        Args:
            analyst: Check one analyst's names, or None to check all of them.

        Returns:
            list: The unmatched names, with duplicates removed. Empty when
                everything resolved, which is the healthy case.
        """
        df = (self._ffb_adapter.load(analyst) if analyst
              else self._ffb_adapter.load_all())
        return self._identity_repo.unresolved_with_fallback("ffb", df["name"], df["position"])

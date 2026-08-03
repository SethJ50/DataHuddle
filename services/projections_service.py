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
    def __init__(self, ffb_adapter, identity_repo, roster_service):
        self._ffb_adapter = ffb_adapter
        self._identity_repo = identity_repo
        self._roster_service = roster_service

    @property
    def analysts(self) -> list:
        """Which analysts are available to select."""
        return self._ffb_adapter.analysts

    def get_own_projections(self, analyst: str = None) -> pd.DataFrame:
        """
        Purpose: Our own season-long and per-game fantasy point projections.

        Parameters:
            analyst (str | None): A single analyst's numbers, or None (default)
                for the blend of all three.

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

        Notes:
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
        """
        Purpose: Attach canonical_id and restrict to the app's player universe.

        Parameters:
            df (pd.DataFrame): Adapter output, with name and position columns.

        Returns:
            pd.DataFrame with canonical_id added; unresolvable players and
            players outside the UDK roster are dropped.

        Notes:
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
        """Add season and per-game fantasy points for every scoring format."""
        df = df.copy()
        stats = {key: df[key] for key in scoring.STAT_KEYS}
        for fmt, season_points in scoring.fantasy_points_all_formats(stats).items():
            df[f"fantasy_points_{fmt.value}_season"] = season_points
            df[f"fantasy_points_{fmt.value}_per_game"] = scoring.per_game(season_points)
        return df

    def _blend(self, scored: pd.DataFrame) -> pd.DataFrame:
        """
        Purpose: Collapse per-analyst rows into one row per player.

        Parameters:
            scored (pd.DataFrame): Long format, one row per (player, analyst),
                already scored.

        Returns:
            pd.DataFrame — one row per canonical_id, carrying the mean of every
            numeric column plus low/high/spread on each format's season points
            and an `n_analysts` count.

        Notes:
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
        """
        Purpose: Where the three analysts disagree most.

        Parameters:
            fmt (ScoringFormat): Which scoring format to measure in.

        Returns:
            pd.DataFrame with canonical_id, name, position, the blended season
            points, low, high, spread and n_analysts — sorted by spread, widest
            first. Players rated by fewer than two analysts are excluded, since
            a spread of zero across one opinion means nothing.

        Notes:
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
        """
        Purpose: FFB projection names that couldn't be matched to a canonical_id
            at all — candidates for a manual player_id_map row.

        Parameters:
            analyst (str | None): Check one analyst, or all of them.

        Returns: list[str], de-duplicated.
        """
        df = (self._ffb_adapter.load(analyst) if analyst
              else self._ffb_adapter.load_all())
        return self._identity_repo.unresolved_with_fallback("ffb", df["name"], df["position"])

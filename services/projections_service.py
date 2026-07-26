"""Computes fantasy point projections from adapters' raw-stat output.

This is the one place "what will this player score" is answered — the
scoring math itself lives in scoring.py (imported here, not duplicated).
Results are resolved to canonical_id and scoped to RosterService's UDK
roster, so "our own projections" and "who's in the app" never drift apart.
"""

import pandas as pd

import scoring


class ProjectionsService:
    def __init__(self, ffb_adapter, identity_repo, roster_service):
        self._ffb_adapter = ffb_adapter
        self._identity_repo = identity_repo
        self._roster_service = roster_service

    def get_own_projections(self) -> pd.DataFrame:
        """
        Purpose: Our own season-long + per-game fantasy point projections
            (all three scoring formats) from Fantasy Footballers/UDK raw
            stats — used for Team Depth Chart ordering and Draft Plan
            "True Value" per PLANNING.md.

        Returns:
            pd.DataFrame — FfbProjectionsAdapter's raw-stat columns plus
            canonical_id and 6 fantasy-point columns (season/per-game for
            regular/half-PPR/full-PPR). Only players resolved to a
            canonical_id AND present in RosterService's UDK roster are
            included — everyone else is dropped, so this stays consistent
            with the rest of the app's player universe.

        Notes:
            FFB's projection rows are matched to canonical_id the same way
            ADP platform rows are: player_id_map first, exact-name (+
            position) fallback second (see PlayerIdentityRepo).
        """
        combined = self._ffb_adapter.load()

        canonical_id = self._identity_repo.resolve_many_with_fallback(
            "ffb", combined["name"], combined["position"]
        )
        combined = combined.assign(canonical_id=canonical_id).dropna(subset=["canonical_id"])

        roster_ids = self._roster_service.canonical_ids()
        combined = combined[combined["canonical_id"].isin(roster_ids)]

        stats = {key: combined[key] for key in scoring.STAT_KEYS}
        for fmt, season_points in scoring.fantasy_points_all_formats(stats).items():
            combined[f"fantasy_points_{fmt.value}_season"] = season_points
            combined[f"fantasy_points_{fmt.value}_per_game"] = scoring.per_game(season_points)

        return combined

    def unresolved(self) -> list:
        """
        Purpose: FFB projection names that couldn't be matched to a
            canonical_id at all -- candidates for a manual player_id_map
            row.
        Returns: list[str], de-duplicated.
        """
        df = self._ffb_adapter.load()
        return self._identity_repo.unresolved_with_fallback("ffb", df["name"], df["position"])
"""Joins Fantasy Football Calculator ADP to the app's canonical player identity.

FFC arrives keyed by its OWN player ids, which mean nothing to the rest of the
app. Everything else here is keyed by `canonical_id` (nflreadpy's gsis_id), so
something has to bridge the two. That's this.

An important asymmetry, and the reason this service does not simply drop
unmatched rows (draft_model/DESIGN.md, invariant 5):

  The SIMULATOR does not need canonical_id. It only needs adp, stdev and
  position. A player who fails to resolve still occupies a pick in every
  simulated draft, which is exactly what we want -- kickers and defenses come off
  the board in real drafts, and pretending otherwise would push ~46 skill players
  artificially later.

  The DISPLAY layer does need canonical_id, because that's how a row joins to
  projections, headshots and your own rankings.

So `canonical_id` is a nullable enrichment, never a filter.
"""

import pandas as pd

from scoring import ScoringFormat

# Positions that CANNOT resolve, by construction rather than by data quality.
# A team defense is not a person and has no gsis_id. Kickers are rostered players
# and generally do resolve, but they are grouped here because neither is needed
# by the simulator and neither is worth a manual player_id_map row.
UNRESOLVABLE_BY_DESIGN = ("DST",)


class FfcService:
    """Reads current FFC ADP and attaches canonical_id where one can be found."""

    def __init__(self, ffc_repo, identity_repo):
        """
        Parameters:
            ffc_repo (FfcRepo): Source of the current-season FFC table.
            identity_repo (PlayerIdentityRepo): Does the name -> canonical_id
                resolution, using the curated player_id_map first and an exact
                name+position match as fallback.
        """
        self._ffc_repo = ffc_repo
        self._identity_repo = identity_repo

    def with_canonical_id(self, fmt: ScoringFormat) -> pd.DataFrame:
        """
        Purpose: The current FFC table with a canonical_id column attached.

        Parameters:
            fmt (ScoringFormat): Which scoring format's pool to return.

        Returns:
            pd.DataFrame -- every FFC row for that format, in ADP order:
                ffc_player_id, name, position, team, adp, stdev, high, low,
                times_drafted, bye, format, season, pulled_at, canonical_id
            `canonical_id` is None wherever resolution failed. NO ROWS ARE
            DROPPED -- see the module docstring for why that matters.
            Empty DataFrame if load_data hasn't pulled this format yet.
        """
        df = self._ffc_repo.current(fmt)
        if df.empty:
            return df

        canonical_id = self._identity_repo.resolve_many_with_fallback(
            "ffc", df["name"], df["position"]
        )
        return df.assign(canonical_id=canonical_id)

    def resolution_report(self, fmt: ScoringFormat) -> dict:
        """
        Purpose:
            Summarize how well FFC joined to the app's player universe, splitting
            the failures into "expected" and "worth your time".

        Parameters:
            fmt (ScoringFormat): Which format's pool to check.

        Returns:
            dict:
                total (int)        -- rows in the FFC pool
                resolved (int)     -- rows that got a canonical_id
                expected (df)      -- unresolved rows at positions that cannot
                                      resolve by construction (team defenses)
                actionable (df)    -- unresolved SKILL players. These are the only
                                      ones worth a manual player_id_map row.
            Both frames carry name, position, team, adp.

        Notes:
            The split exists so the actionable list stays readable. Lumping team
            defenses in with genuine misses buries the handful of real problems
            under a few dozen rows that will never resolve no matter what you do.

            An empty `actionable` list is the healthy state. A growing one usually
            means a rookie or a recent signing that nflreadpy hasn't picked up yet,
            not a bug.
        """
        df = self.with_canonical_id(fmt)
        if df.empty:
            return {"total": 0, "resolved": 0,
                    "expected": pd.DataFrame(), "actionable": pd.DataFrame()}

        columns = ["name", "position", "team", "adp"]
        missing = df[df["canonical_id"].isna()]
        by_design = missing["position"].isin(UNRESOLVABLE_BY_DESIGN)

        return {
            "total": len(df),
            "resolved": int(df["canonical_id"].notna().sum()),
            "expected": missing.loc[by_design, columns].reset_index(drop=True),
            "actionable": missing.loc[~by_design, columns].reset_index(drop=True),
        }

import pandas as pd
from scoring import ScoringFormat

class DraftPlanService:
    def __init__(self, roster_service, adp_comparison_service, projections_service):
        self._roster_service = roster_service
        self._adp_comparison_service = adp_comparison_service
        self._projections_service = projections_service

    def pick_labels(self, num_teams, draft_position, num_rounds):
        """
        Purpose: Works out exactly which overall pick number is yours in
        every round of a snake draft.
        """

        picks = []
        for round_number in range(1, num_rounds + 1):
            is_even_round = round_number % 2 == 0
            pick_in_round = (num_teams - draft_position + 1) if is_even_round else draft_position
            overall_pick = (round_number - 1) * num_teams + pick_in_round

            picks.append({
                "round": round_number,
                "pick_in_round": pick_in_round,
                "overall_pick": overall_pick,
                "label": f"{round_number}.{pick_in_round:02d}"
            })
        
        return picks

    def rank_candidates(self, position, platform, fmt):
        """
        Purpose: For one position, builds a table of every roster-eligible
            player with their platform ADP, your own projected fantasy
            points, and how those two rankings compare -- this is what
            powers the Player dropdown and the ADP/True Value/Diff columns
            in each position's data_editor grid.
        Parameters:
            position (str): "QB", "RB", "WR", or "TE".
            platform (str): "espn", "yahoo", or "sleeper" -- picks which
                platform's ADP column to use.
            fmt (ScoringFormat): HALF_PPR or FULL_PPR -- affects both which
                ADP number is used (ESPN/Sleeper track separate half/full-PPR
                ADP) and which of your own projected-points columns ranks
                "True Value".
        Returns:
            pd.DataFrame with columns: canonical_id, display_name, adp,
            adp_rank, projected_points, true_value_rank, diff. Sorted by
            true_value_rank (your own best-projected player at this position
            first).
        Notes:
            `diff` = adp_rank - true_value_rank. A positive number means your
            own projections rank the player better (a lower rank number)
            than the field's ADP does -- i.e. a player you like more than the
            market does. Both ranks are computed within this one position
            only, so a Diff of "+5" always means "5 spots better than ADP
            among players at this same position," never a cross-position
            comparison.
        """

        roster = self._roster_service.roster()
        roster = roster[roster["position"] == position][["canonical_id", "display_name"]]

        adp_column = {"espn": "espn_adp", "yahoo": "yahoo_adp", "sleeper": "sleeper_adp"}[platform]
        comparison = self._adp_comparison_service.compare(fmt)
        adp = comparison[["canonical_id", adp_column]].rename(columns={adp_column: "adp"})

        points_column = (
            "fantasy_points_half_ppr_season" if fmt == ScoringFormat.HALF_PPR
            else "fantasy_points_full_ppr_season"
        )
        projections = self._projections_service.get_own_projections()
        points = projections[["canonical_id", points_column]].rename(
            columns={points_column: "projected_points"}
        )

        candidates = roster.merge(adp, on="canonical_id", how="left").merge(
            points, on="canonical_id", how="left"
        )

        # rank(method="min") means tied values share the best rank instead of
        # breaking ties arbitrarily -- e.g. two players tied for the best ADP
        # both get rank 1, not 1 and 2.
        candidates["adp_rank"] = candidates["adp"].rank(method="min")
        candidates["true_value_rank"] = candidates["projected_points"].rank(method="min", ascending=False)
        candidates["diff"] = candidates["adp_rank"] - candidates["true_value_rank"]

        return candidates.sort_values("true_value_rank")
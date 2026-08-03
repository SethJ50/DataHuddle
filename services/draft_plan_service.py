import pandas as pd
from datetime import datetime, timezone

from scoring import ScoringFormat
from registry import Collections
from db.documents import find_one, upsert

class DraftPlanService:
    def __init__(self, roster_service, adp_comparison_service, projections_service):
        self._roster_service = roster_service
        self._adp_comparison_service = adp_comparison_service
        self._projections_service = projections_service

    def save_plan(self, draft_id, plans):
        """
        Purpose:
            Persist a draft's whole board plan -- every round/position's selected
            players, in priority order -- to Mongo, so it survives page reloads.

        Parameters:
            draft_id (str): The draft these selections belong to.
            plans (dict): The in-session store, keyed by a (round_label, position)
                tuple -> list of player display names in priority order. Shape:
                {("1.04", "QB"): ["Player A", "Player B"], ...}.

        Returns:
            None. Writes one document per draft into the draft_plans collection.

        Notes:
            Mongo can't use tuple keys, so the dict is flattened into a list of
            {round, position, players} entries. Empty selections are dropped to
            keep the document tidy. Upsert keyed on draft_id means re-saving
            overwrites the previous plan rather than piling up documents.
        """
        entries = [
            {"round": round_label, "position": position, "players": players}
            for (round_label, position), players in plans.items()
            if players  # skip rounds/positions with nothing selected
        ]

        doc = {
            "draft_id": draft_id,
            "entries": entries,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        upsert(Collections.DRAFT_PLANS, {"draft_id": draft_id}, doc)

    def get_plan(self, draft_id):
        """
        Purpose:
            Load a draft's saved board plan back into the shape the page uses,
            so selections can be restored when the page (re)loads.

        Parameters:
            draft_id (str): The draft whose plan to load.

        Returns:
            dict keyed by (round_label, position) tuple -> list of player names,
            matching what save_plan() stored. Empty dict if this draft has no
            saved plan yet.

        Notes:
            Rebuilds the tuple keys from the flat {round, position, players}
            entries written by save_plan().
        """
        doc = find_one(Collections.DRAFT_PLANS, {"draft_id": draft_id})
        if not doc:
            return {}

        return {
            (entry["round"], entry["position"]): entry["players"]
            for entry in doc.get("entries", [])
        }

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
        # TODO - TrueVal
        # - for yahoo is adjusted adp for keep
        # - for NFL was difference between NFL platform and FFB
        # - Future - should be platform rank adjusted for keepers, and diff is diff from FFB rank overall?
        candidates["true_value_rank"] = candidates["projected_points"].rank(method="min", ascending=False)
        candidates["diff"] = candidates["adp_rank"] - candidates["true_value_rank"]

        return candidates.sort_values("true_value_rank")
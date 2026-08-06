"""Builds and saves the user's pre-draft plan: who to target at each pick.

Two jobs live here. Saving and loading the plan itself, which is a list of
players the user picked out for each round and position, and building the
candidate tables that plan is made from — a per-position ranking that puts
platform ADP next to your own projections so the gaps are visible.
"""

import pandas as pd
from datetime import datetime, timezone

from scoring import ScoringFormat
from registry import Collections
from db.documents import find_one, upsert

class DraftPlanService:
    """Backs the Draft Plan page: candidate rankings, plus saving the plan.

    `rank_candidates` produces the tables the user picks from, `pick_labels`
    works out which picks are theirs, and `save_plan`/`get_plan` make the
    selections survive a page reload.
    """

    def __init__(self, roster_service, adp_comparison_service, projections_service):
        """Store the three services this one builds its tables from.

        Steps:
            1. Save each collaborator on the instance. Nothing is loaded yet.

        Args:
            roster_service: Supplies the in-scope player list.
            adp_comparison_service: Supplies each platform's ADP per player.
            projections_service: Supplies your own projected fantasy points.
        """
        self._roster_service = roster_service
        self._adp_comparison_service = adp_comparison_service
        self._projections_service = projections_service

    def save_plan(self, draft_id, plans):
        """Save a draft's whole board plan so it survives a page reload.

        Streamlit forgets everything when a page reloads, so the plan has to live
        in the database rather than in memory.

        Steps:
            1. Flatten the in-memory dictionary into a list of flat records,
               skipping any round and position with nothing selected.
            2. Wrap those in a document tagged with the draft id and the current
               UTC time.
            3. Call `upsert` from db/documents.py keyed on the draft id, so
               re-saving replaces the previous plan rather than piling up
               documents.

        Args:
            draft_id: The draft these selections belong to.
            plans: The in-session store, keyed by a `(round_label, position)`
                tuple with a list of player display names in priority order as
                the value. For example
                `{("1.04", "QB"): ["Player A", "Player B"], ...}`.

        Returns:
            None: Writes one document per draft into the draft_plans collection.

        Note:
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
        """Load a draft's saved plan back into the shape the page expects.

        The exact inverse of `save_plan` above, so what the page reads back is
        what it handed over.

        Steps:
            1. Call `find_one` from db/documents.py, matching on the draft id.
            2. If nothing is stored, return an empty dictionary, since no saved
               plan is normal rather than an error.
            3. Rebuild the tuple keys by pairing each record's round and position
               back together.

        Args:
            draft_id: The draft whose plan to load.

        Returns:
            dict: Keyed by a `(round_label, position)` tuple with a list of
                player names as the value, exactly matching what `save_plan`
                stored. Empty if this draft has no saved plan yet.
        """
        doc = find_one(Collections.DRAFT_PLANS, {"draft_id": draft_id})
        if not doc:
            return {}

        return {
            (entry["round"], entry["position"]): entry["players"]
            for entry in doc.get("entries", [])
        }

    def pick_labels(self, num_teams, draft_position, num_rounds):
        """Work out which overall pick number is yours in every round.

        The Draft Plan page is organized by your picks, so it needs both the
        overall number and a readable label like "3.09" for each round.

        Steps:
            1. Walk the rounds from 1 to `num_rounds`.
            2. Work out where you sit within that round. Odd rounds run forward,
               so it is your draft position; even rounds run backward, so count
               in from the other end.
            3. Convert that to an overall pick number by adding the picks made in
               all previous rounds.
            4. Build a record holding the round, the position within it, the
               overall number, and a "round.pick" label padded to two digits.

        Args:
            num_teams: How many teams are in the league.
            draft_position: Your slot, 1-indexed.
            num_rounds: How many rounds are drafted.

        Returns:
            list: One dictionary per round, each with `round`, `pick_in_round`,
                `overall_pick`, and `label` (a string such as "3.09").

        Note:
            This computes snake order independently of
            `draft_model.mechanics.picks_for_slot`, which does the same job for
            the simulator. This version also produces the display label and does
            not handle third-round reversal; that one handles reversal and
            returns bare numbers.
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
        """Rank one position's players by your projections against platform ADP.

        This is what powers the player dropdown and the ADP / True Value / Diff
        columns in each position's grid on the Draft Plan page. Putting the two
        rankings side by side is how you spot players the market is mispricing
        relative to your own numbers.

        Steps:
            1. Get the in-scope players from the roster service, keep only this
               position, and carry UDK's `tier` along with the name.
            2. Pick the ADP column matching the chosen platform, load the
               comparison table, and rename that column to plain `adp`.
            3. Pick the projected-points column matching the scoring format, load
               your own projections, and rename it to `projected_points`.
            4. Merge both onto the roster with left joins, so a player missing
               from either source still appears with a blank.
            5. Rank by ADP ascending, since a lower ADP is better.
            6. Rank by projected points descending, since more points is better,
               giving the "true value" rank.
            7. Subtract the two ranks to get `diff`.

        Args:
            position: Which position to build the table for: "QB", "RB", "WR",
                or "TE".
            platform: Which platform's ADP to use: "espn", "yahoo", or
                "sleeper".
            fmt: HALF_PPR or FULL_PPR. This affects both which ADP number is used,
                since ESPN and Sleeper track separate half and full-PPR ADP, and
                which of your projected-points columns ranks "True Value".

        Returns:
            pd.DataFrame: Columns `canonical_id`, `display_name`, `tier`, `adp`,
                `adp_rank`, `projected_points`, `true_value_rank`, and `diff`,
                sorted so your own best-projected player at this position comes
                first. `tier` is UDK's grouping within this position, a small
                whole number where 1 is the best group. `projected_points` is the
                Fantasy Footballers blend for the requested scoring format, and
                is blank for the handful of ranked players the analysts do not
                cover.

        Raises:
            KeyError: If `platform` is not one of the three platform names.

        Note:
            Both ranks use `method="min"`, so tied values share the better rank
            instead of being separated arbitrarily — two players tied for the
            best ADP both get rank 1, not 1 and 2.

            `diff` = adp_rank - true_value_rank. A positive number means your
            own projections rank the player better (a lower rank number)
            than the field's ADP does -- i.e. a player you like more than the
            market does. Both ranks are computed within this one position
            only, so a Diff of "+5" always means "5 spots better than ADP
            among players at this same position," never a cross-position
            comparison.
        """

        roster = self._roster_service.roster()
        # `tier` rides along from UDK's rankings. It is a grouping WITHIN a
        # position -- tier 1 running backs are not comparable to tier 1 receivers
        # -- which is fine here because this table is always one position.
        roster = roster[roster["position"] == position][
            ["canonical_id", "display_name", "tier"]
        ]

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
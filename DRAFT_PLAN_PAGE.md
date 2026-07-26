# Draft Plan Page

## Feature Overview

This page lets you plan out your own picks for an upcoming fantasy draft: for
every round you'll actually pick in (computed from league size + your draft
slot), you can list one or more candidate players per position you're
considering, compare them side by side (their platform ADP vs. your own
projections), and mark which one you're actually planning to take.

This reconciles your sketch in this file with the earlier, rougher spec for
this page in `PLANNING.md`'s "Draft Plan" section — that version assumed one
fixed player per position per round; your sketch is richer (a shortlist per
pick you can compare and narrow down), and this doc goes with your sketch.

**Decisions made** (from the clarifying questions):
- **Pick rows = your own picks only**, computed from *Teams in League* +
  *Draft Position* via standard snake-draft math (round 1 goes in draft-slot
  order, round 2 reverses, and so on) — not the full multi-team draft board.
- **Each position's cell is a shortlist, not a single answer.** You can add
  any number of candidate rows for a given pick and position, compare them,
  and mark one as your actual planned pick once you decide.
- **Scope for tomorrow: one working board only.** No saved/named/duplicate
  draft plans yet (that's the `draft_plans` Mongo collection from
  `PLANNING.md` — deferred until the core grid interaction feels right).
- **Row columns**: Player, this pick's platform ADP, True Value (rank from
  your own `FfbData`/`ProjectionsService` projections), Diff (ADP rank minus
  True Value rank — positive means your projections like the player *more*
  than their ADP suggests), and a marking tag (Safe / Upside / Late / Early),
  matching the original `PLANNING.md` spec.

**Two small additions beyond your original sketch**, both needed to make the
board well-defined rather than ambiguous choices on my part:
- **Number of Rounds** input, alongside Teams in League/Draft Position/
  Platform. Snake-draft math needs a round count to know how many pick rows
  to generate; defaulting to 15 (a typical season-long roster's rough size)
  but fully editable.
- **Scoring Format** toggle (Half PPR / Full PPR), matching the existing ADP
  Comparison and Team Depth Charts pages. It's needed for two things: ESPN and
  Sleeper both track *different* ADP numbers per format (same pattern as
  `adapters/adp_source_adapter.py`), and your own projections' point totals
  (used for True Value) also differ half-PPR vs. full-PPR.
- **A "Locked In" checkbox column**, separate from the Safe/Upside/Late/Early
  marking — since a pick can have several candidates listed, this is the flag
  that says "this one is the actual plan," distinct from describing a
  candidate's risk profile.

**Positions covered: QB, RB, WR, TE only** (no K/DST) — matching
`RosterService`'s existing player universe, which is itself scoped to UDK's
rankings (UDK doesn't rank kickers/defenses, so neither does anything else in
this app).

## TODO List

1. Add `services/draft_plan_service.py` — snake-draft pick-label math, plus a
   candidate-ranking helper that combines `RosterService`, `AdpComparisonService`,
   and `ProjectionsService` (all already built, see Implementation Guide below).
2. Build the Streamlit prototype, `streamlit_poc/draft_plan_app.py`, matching
   the existing `streamlit_poc/adp_comparison_app.py`'s pattern (reuses
   `AppContext`, cached via `@st.cache_resource`/`@st.cache_data` the same way).
3. Four inputs at the top: Teams in League, Draft Position, Number of Rounds,
   Platform, Scoring Format.
4. One `st.data_editor` grid per position (QB/RB/WR/TE), with editable
   Pick/Player/Marking/Locked In columns and computed (disabled) ADP/True
   Value Rank/Diff columns that recompute whenever the Player column changes.
5. Manually test: add/remove candidate rows; change Platform and Scoring
   Format and confirm ADP/True Value/Diff recompute correctly; sanity-check
   the pick labels for a couple of different (teams, draft position)
   combinations against a hand-worked snake-draft order.
6. **Explicitly out of scope for tomorrow**: persisting to MongoDB's
   `draft_plans` collection, named/saved/duplicate plans, and the landing
   page listing saved plans. Revisit once the single-board interaction feels
   right — the data model below is designed so that persistence layer can be
   added later without reshaping anything already built.

## Implementation Guide

### 1. Page inputs

```
Teams in League: [ ]   Draft Position: [ ]   Rounds: [ ]
Platform: [ESPN | Yahoo | Sleeper]   Scoring Format: [Half PPR | Full PPR]
```

Nothing new to build here — this is the same shape of control row as
`streamlit_poc/adp_comparison_app.py`'s existing `st.columns(3)` block, just
with two more `st.number_input`s (Teams in League, Rounds) alongside the
existing `st.selectbox` pattern.

### 2. New service: `services/draft_plan_service.py`

This is where the snake-draft math and the candidate-ranking logic live, kept
out of the Streamlit script itself — same reasoning as every other page in
this app (`ARCHITECTURE.md`'s "services do the math" layer): if this page
ever also needs a Shiny version, or gets unit tests, none of this logic has
to move.

```python
"""
Supports the Draft Plan page: turns (teams, draft position, rounds) into a
list of your own picks in snake-draft order, and ranks candidate players for
one pick's position by comparing their platform ADP against your own
projections.
"""

import pandas as pd
from scoring import ScoringFormat


class DraftPlanService:
    def __init__(self, roster_service, adp_comparison_service, projections_service):
        self._roster_service = roster_service
        self._adp_comparison_service = adp_comparison_service
        self._projections_service = projections_service

    def pick_labels(self, num_teams: int, draft_position: int, num_rounds: int) -> list[dict]:
        """
        Purpose: Works out exactly which overall pick number is yours in
            every round of a snake draft, given your league size and draft
            slot.
        Parameters:
            num_teams (int): how many teams are in the league.
            draft_position (int): your draft slot, 1-indexed (e.g. 1 means
                you pick first in round 1).
            num_rounds (int): how many rounds to generate picks for.
        Returns:
            list[dict], one entry per round, each shaped
            {"round": int, "pick_in_round": int, "overall_pick": int, "label": str}.
            `label` is the human-readable "round.pick" form (e.g. "2.09").
        Notes:
            In a snake draft, odd rounds go in normal draft-slot order (1, 2,
            3, ...) and even rounds reverse (..., 3, 2, 1) -- so your own
            pick number within the round flips between `draft_position` and
            `num_teams - draft_position + 1` depending on whether the round
            is odd or even. `overall_pick` counts every pick from the very
            start of the draft, which is what `(round - 1) * num_teams`
            handles -- it's the number of picks already made by every team
            in every earlier round.
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
                "label": f"{round_number}.{pick_in_round:02d}",
            })

        return picks

    def rank_candidates(self, position: str, platform: str, fmt: ScoringFormat) -> pd.DataFrame:
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
```

**Wiring into `AppContext`** — add alongside the other services:
```python
self.draft_plan_service = DraftPlanService(
    self.roster_service,
    self.adp_comparison_service,
    self.projections_service,
)
```

### 3. Streamlit layout — one `data_editor` per position

The four positions become four tabs (or expanders — either works; tabs keep
the page shorter). Each tab holds one editable grid: Pick, Player, ADP, True
Value Rank, Diff, Marking, Locked In. Only Pick/Player/Marking/Locked In are
ever typed or chosen by you — ADP/True Value Rank/Diff are always looked up
from whichever player is currently selected in that row, never hand-entered.

```python
import pandas as pd
import streamlit as st
from scoring import ScoringFormat

MARKING_OPTIONS = ["Safe", "Upside", "Late", "Early"]
BOARD_COLUMNS = ["Pick", "Player", "ADP", "True Value Rank", "Diff", "Marking", "Locked In"]

# ... after collecting num_teams, draft_position, num_rounds, platform,
# scoring_format from the input widgets ...

pick_options = [
    p["label"] for p in ctx.draft_plan_service.pick_labels(num_teams, draft_position, num_rounds)
]

for position in ["QB", "RB", "WR", "TE"]:
    with st.expander(position, expanded=True):
        candidates = ctx.draft_plan_service.rank_candidates(position, platform, scoring_format)
        # A lookup from display name -> that player's row, so we can refill
        # ADP/True Value Rank/Diff after any edit just by looking at whichever
        # name is currently in the Player column.
        by_name = candidates.set_index("display_name")

        # st.data_editor only ever gives back its LATEST snapshot on each
        # rerun -- it doesn't remember earlier edits by itself. Keeping each
        # position's board in st.session_state is what makes rows you added
        # a moment ago still be there on the next rerun (e.g. after you pick
        # a different Player in some other row).
        state_key = f"draft_board_{position}"
        if state_key not in st.session_state:
            st.session_state[state_key] = pd.DataFrame(columns=BOARD_COLUMNS)

        edited = st.data_editor(
            st.session_state[state_key],
            column_config={
                "Pick": st.column_config.SelectboxColumn(options=pick_options, required=True),
                "Player": st.column_config.SelectboxColumn(options=list(by_name.index), required=True),
                "ADP": st.column_config.NumberColumn(disabled=True),
                "True Value Rank": st.column_config.NumberColumn(disabled=True),
                "Diff": st.column_config.NumberColumn(disabled=True),
                "Marking": st.column_config.SelectboxColumn(options=MARKING_OPTIONS),
                "Locked In": st.column_config.CheckboxColumn(),
            },
            num_rows="dynamic",  # lets you add/remove candidate rows freely
            hide_index=True,
            key=f"editor_{position}",
        )

        # Recompute the three derived columns for every row based on
        # whichever player that row currently has selected. Rows with no
        # Player chosen yet (a freshly added blank row) are simply left with
        # blank/NaN values here until a player is picked.
        edited["ADP"] = edited["Player"].map(by_name["adp"])
        edited["True Value Rank"] = edited["Player"].map(by_name["true_value_rank"])
        edited["Diff"] = edited["Player"].map(by_name["diff"])

        st.session_state[state_key] = edited
```

**Context check on `st.data_editor` + `st.session_state`**: unlike a normal
variable, `st.session_state` survives *between* reruns of the whole script
(Streamlit reruns the entire file top-to-bottom on every widget interaction —
see `datahuddle/streamlit_vs_shiny_decision.md` for more on that model). Using
it here is exactly the same idea as `get_app_context()`'s `@st.cache_resource`
elsewhere in this app: a way to keep something alive across reruns that would
otherwise be rebuilt from scratch every time you touch any widget on the page.

### 4. Data model to keep in mind for later (not built tomorrow)

When persistence gets added, one saved draft plan should look like this in
MongoDB's `draft_plans` collection (via `db/documents.py`'s existing
`upsert`/`find_one`, the same generic one-document-at-a-time storage already
used elsewhere) — designed so today's in-memory `st.session_state` boards can
be dropped in directly as the `candidates` list with no reshaping:

```
{
  name: str,
  num_teams: int,
  draft_position: int,
  num_rounds: int,
  platform: str,
  scoring_format: str,
  boards: {
    "QB": [ {pick_label, canonical_id, marking, locked_in}, ... ],
    "RB": [ ... ],
    "WR": [ ... ],
    "TE": [ ... ],
  }
}
```

Only `canonical_id` needs to be stored per row — display name, ADP, True
Value, and Diff are always re-derived from `DraftPlanService.rank_candidates()`
at load time, the same way this app avoids storing anything that can be
looked up fresh instead (matching `PlayerDirectory`/`RosterService`'s existing
pattern of keying everything off `canonical_id` rather than duplicating
display data).

## Verification Checklist (for tomorrow)

- [ ] Pick labels are correct for at least two different (teams, draft
      position) combinations — hand-check the first 3-4 rounds against a
      known snake-draft order.
- [ ] Adding a row, picking a Player, confirms ADP/True Value Rank/Diff fill
      in correctly and match what `rank_candidates()` returns directly.
- [ ] Switching Platform changes ADP (and Diff, since it depends on ADP rank)
      without losing any rows you've already added.
- [ ] Switching Scoring Format changes both ADP (for ESPN/Sleeper) and True
      Value/Diff, without losing rows.
- [ ] Removing a row and adding a new one for the same Pick still works (the
      "shortlist per pick" behavior).
- [ ] A player with no ADP or no projection on record shows a blank (not a
      crash) in the relevant column.

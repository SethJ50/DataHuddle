# Draft Plan UI — Position-First Tabs Layout

## Feature Overview

The Draft Plan Streamlit prototype (`streamlit_poc/draft_plan_app.py`)
currently lays out one `st.expander` per pick, with the four positions
(QB/RB/WR/TE) squeezed side-by-side into `st.columns(4)` inside each
expander. Even in Streamlit's "wide" page mode, six columns
(Player/ADP/True Value Rank/Diff/Marking/Locked In) times four positions
doesn't fit — the Marking and Locked In columns get cut off, as seen when
comparing against the Google Sheet this tool is meant to replace.

This feature flips the layout's main axis from **pick-first** to
**position-first**: instead of one section per pick containing four narrow
position tables, the page becomes four tabs (QB, RB, WR, TE), and each tab
holds a single full-width table listing every pick's shortlist for that one
position. A new "Pick" column (a dropdown of round.pick labels like `"2.11"`)
tags each row so you still know which round a candidate belongs to — it's
just a column now instead of a heading.

This was actually the layout `DRAFT_PLAN_PAGE.md`'s original implementation
guide described; the version that got built diverged into the per-pick
column grid. This change brings the code back in line with that plan and
fixes the horizontal-space problem in the process.

No changes are needed to `services/draft_plan_service.py` — `pick_labels()`
and `rank_candidates()` already return everything this layout needs
unchanged.

## TODO List

1. Add a `Pick` column back to `BOARD_COLUMNS`, as an editable
   `SelectboxColumn` populated from `pick_labels()`.
2. Replace the "one `st.expander` per pick, `st.columns(4)` per position"
   loop with "one `st.tabs` per position, one full-width `data_editor` per
   tab covering every pick."
3. Move each position's `st.session_state` key from being keyed by
   `(pick, position)` to being keyed by `position` alone, since there's now
   only one table per position instead of one per (pick, position) pair.
4. Manually verify: adding/removing rows in a tab; picking a `Pick` value
   and a `Player` value independently; switching Platform/Scoring Format
   still recomputes ADP/True Value Rank/Diff without losing existing rows;
   switching tabs preserves each position's table.

## Implementation Guide

### 1. Add "Pick" to the row schema

```python
# Pick is now a real column (a dropdown of round.pick labels), since a
# position's whole draft-long shortlist lives in one table instead of being
# implied by which pick's expander the table is nested inside.
BOARD_COLUMNS = ["Pick", "Player", "ADP", "True Value Rank", "Diff", "Marking", "Locked In"]
```

**Context check:** `pick_labels()` (from `services/draft_plan_service.py`)
returns a list of dicts like `{"round": 2, "pick_in_round": 11, "overall_pick": 23, "label": "2.11"}`.
We only need the human-readable `"label"` values for the dropdown, so we
pull just that field into a plain list of strings:

```python
pick_labels_list = ctx.draft_plan_service.pick_labels(num_teams, draft_position, num_rounds)
pick_options = [p["label"] for p in pick_labels_list]
```

### 2. Position tabs instead of pick expanders

```python
# st.tabs() draws one clickable tab per string in the list you give it, and
# returns one "tab container" object per tab, in the same order. Whatever
# you put inside a `with tab:` block only shows up when that tab is active
# -- this is what gives each position's table the FULL page width, since
# only one tab's content is on screen at a time (unlike st.columns, which
# splits the width between everything shown at once).
tabs = st.tabs(POSITIONS)

for tab, position in zip(tabs, POSITIONS):
    with tab:
        by_name = by_name_by_position[position]

        # One state key per position now (not per pick+position), since
        # this tab holds every pick's shortlist for this position together.
        state_key = f"draft_board_{position}"
        if state_key not in st.session_state:
            st.session_state[state_key] = pd.DataFrame(columns=BOARD_COLUMNS)

        edited = st.data_editor(
            st.session_state[state_key],
            column_config={
                # New: Pick is now its own editable dropdown column, using
                # the round.pick labels computed above.
                "Pick": st.column_config.SelectboxColumn(options=pick_options, required=True),
                "Player": st.column_config.SelectboxColumn(options=list(by_name.index), required=True),
                "ADP": st.column_config.NumberColumn(disabled=True),
                "True Value Rank": st.column_config.NumberColumn(disabled=True),
                "Diff": st.column_config.NumberColumn(disabled=True),
                "Marking": st.column_config.SelectboxColumn(options=MARKING_OPTIONS),
                "Locked In": st.column_config.CheckboxColumn(),
            },
            num_rows="dynamic",
            hide_index=True,
            key=f"editor_{position}",
        )

        # Same recompute-on-edit pattern as before: ADP/True Value Rank/Diff
        # are never hand-typed, always looked up from whichever player is
        # currently selected in that row.
        edited["ADP"] = edited["Player"].map(by_name["adp"])
        edited["True Value Rank"] = edited["Player"].map(by_name["true_value_rank"])
        edited["Diff"] = edited["Player"].map(by_name["diff"])

        st.session_state[state_key] = edited
```

**Context check on why this fixes the horizontal squeeze:** `st.columns(4)`
divides whatever width it's given into four equal slices, all visible at
once — that's what was cramming six columns' worth of table into a quarter
of the page. `st.tabs()` instead shows one full-width section at a time and
hides the rest, so every column in the active position's table gets the
entire page width, no matter how many positions exist.

**Trade-off to keep in mind:** because each pick's four positions now live
on different tabs instead of side-by-side, comparing "all my candidates for
pick 2.11" requires switching tabs rather than one glance. That's the
deliberate trade this layout makes in exchange for never running out of
horizontal room.
# Draft Runner — Improvements Plan

Nine changes to `pages/draft_runner.py` and the code behind it, grouped into six
phases. Same conventions as `DRAFT_RUNNER_PLAN.md`: each phase says what to
build, why, and how to know it works before moving on. Code shown is a skeleton
— the bodies are yours.

---

## 1. The nine changes

| # | Change | Phase |
|---|---|---|
| 3 | `Avail` should mean the right pick when it's your turn | 1 |
| 7 | Cost of waiting follows the team on the clock | 1 |
| 1 | Button for a pick not on the list (live drafts) | 2 |
| 6 | Position filter in the console | 2 |
| 5 | Colour draft board cells by position | 3 |
| 8 | Equal draft board column widths | 3 |
| 4 | Cost of waiting as coloured bars, not numbers | 4 |
| 2 | See the board and console at the same time | 5 |
| 9 | Highlight players from your draft plan | 6 |

### Decisions already made

- **Cost of waiting anchors on the team on the clock.** At pick 10 it describes
  team 10's decision; at pick 12 it switches to yours.
- **Combined view is board on top, console below.** Both need horizontal room.
- **Plan highlighting uses the round of your NEXT pick**, since that is how the
  plan was organised when you built it.
- **An unknown pick also captures a position**, so the team's roster count stays
  right and the simulator keeps modelling their needs correctly.

---

## 2. Two things to understand first

### 2.1 Why item 3 is a bug, not a preference

`Avail` currently always shows `P@mine[0]` — the chance a player lasts to your
next pick. The moment your pick actually arrives, `mine[0]` **is** the current
pick, so the column reads ~100% for everyone still on the board:

```
pick   1 (team 1)      Avail = P@4
pick   4 (team 4 = YOU) Avail = P@4   <- this very pick: useless
pick  11 (team 11)     Avail = P@21
```

The column goes blank of information exactly when you are making a decision. The
fix is to look one pick further ahead when it is your turn.

### 2.2 The name-matching trap in item 9

Draft plans store player **display names** — nflreadpy's spelling, via
`roster_service`. The console works from `board.table["name"]` — FFC's spelling.
The two disagree on suffixes and accents:

```
draft plan:    "Kenneth Walker III"
board.table:   "Kenneth Walker"
```

Matching on names would silently miss players, and a highlight that is sometimes
missing is worse than none. Resolve plan names to `canonical_id` through
`roster_service` first, then match on the id. Phase 6 does this.

---

## 3. Phases

Each phase ends with something you can check. Phase 4 depends on 1 and 3; the
rest are independent, so the order is a suggestion rather than a constraint.

---

### Phase 1 — What the numbers mean

**Goal:** `Avail` and the cost-of-waiting panel describe the right pick.

**Files:** `services/draft_runner_service.py`, `pages/draft_runner.py`,
`tests/test_draft_runner_live.py`

#### Step 1.1 — Which pick should `Avail` measure?

Add to the service:

```python
def avail_target_pick(state):
    """Which pick the console's availability column should be measured at.

    Normally your next pick -- "will he still be there when I am up?". But when
    it IS your pick, that question has a trivial answer: he is on the board right
    now, so every available player reads ~100% and the column stops telling you
    anything. On your turn the useful question is the NEXT one: "if I pass on
    him, does he come back to me?"

    Returns:
        int | None: The pick number to measure at, or None when there is nothing
            useful to show -- your last pick, with no later turn to wait for.
    """
```

Sub-steps:

1. Get `remaining_picks(state)`.
2. Return None if it is empty.
3. If the team on the clock is yours **and** you have a later pick, return the
   second entry.
4. Otherwise return the first.
5. If it is your turn and you have no later pick, return None — the caller drops
   the column rather than showing a meaningless one.

#### Step 1.2 — Cost of waiting for any team

The current `positional_costs` is hardwired to your picks. Generalise it.

```python
def team_picks_from(state, team_slot, from_pick):
    """The picks a team still owns, at or after a given pick number."""

def positional_costs_for_team(state, board, picks, team_slot):
    """What waiting costs the given team, from their current turn to their next."""
```

Sub-steps:

1. `team_picks_from` calls `picks_for_slot` from `draft_model/mechanics.py` —
   the one place snake order lives — and filters to picks at or after
   `from_pick`. Remember `picks_for_slot` takes a **1-indexed** slot.
2. `positional_costs_for_team` gets that team's remaining picks, and returns an
   empty frame if fewer than two remain.
3. The rest is the existing `positional_costs` body with `at_pick` and
   `next_pick` coming from that team rather than from you.
4. Keep `positional_costs` as a thin wrapper calling the new one with
   `state.config.draft_position`, so nothing else breaks.

#### Step 1.3 — Wire the page up

1. Replace `next_pick_column = f"P@{mine[0]}"` with `avail_target_pick(state)`.
2. Drop the `Avail` column entirely when it returns None.
3. Update the column's `help` text to name the pick it is measuring.
4. Call `positional_costs_for_team(state, board, picks, state.on_the_clock)`.
5. Caption it with whose decision it is — "Team 10's cost of waiting" versus
   "Your cost of waiting" — or the panel silently changes meaning as the draft
   moves.

#### Step 1.4 — Tests

- On your turn with picks remaining, `avail_target_pick` returns your **second**
  remaining pick, not the current one.
- Not on your turn, it returns your next pick.
- On your final pick it returns None.
- `positional_costs_for_team` gives different answers for two different teams at
  the same board state.
- It returns an empty frame for a team with one pick left.
- `positional_costs` still matches `positional_costs_for_team(..., your slot)`.

**Done when:** the panel's caption names the team on the clock, and `Avail`
stops reading 100% on your own turn.

---

### Phase 2 — Console usability

**Goal:** you can record any pick, and narrow the list to one position.

**Files:** `services/draft_runner_service.py`, `pages/draft_runner.py`, tests

#### Step 2.1 — Let a pick carry a position

`make_pick` already accepts `source="unknown"` for a player you cannot name.
Extend it to record a position too, so the team's roster count stays honest.

Sub-steps:

1. Add a `position=None` argument to `make_pick`.
2. Validate it against `POSITIONS` when given, so a typo fails loudly instead of
   silently never matching.
3. Store it on the entry.
4. In `roster_counts`, when a pick has no matching table row but **does** carry a
   position, tally it anyway: look the position up in `POSITIONS` to get its
   number, then increment as usual.
5. Leave the existing skip for picks with neither.

This is the one change here that touches the simulation. Without it, an
opponent's unlisted kicker leaves their roster count short and the model keeps
thinking they still need one.

#### Step 2.2 — The button

In the page, live mode only, beside Undo/Rewind:

```python
with st.popover("Pick not listed"):
    position = st.selectbox("Position", POSITIONS)
    if st.button("Record pick"):
        state.make_pick(source="unknown", position=position)
        persist()
        st.rerun()
```

Sub-steps:

1. Show it only in Live Draft mode — in a sim the AI picks, so it cannot arise.
2. Loop `apply_keeper_if_due` afterwards, as every other pick path does.
3. Show these on the board as `Unknown (K)` rather than an em dash, by extending
   `entries_from_pick_log` to use the stored position.

#### Step 2.3 — Position filter

Sub-steps:

1. Add `st.pills("Position", POSITIONS, selection_mode="multi")` above the
   search box.
2. Filter `available` before building the grid — after the search filter, so the
   two combine.
3. No selection means no filtering, which is the natural default.
4. Filter the DataFrame, never the display: the click handler indexes into the
   frame you passed in, so what you pass must be what is shown.

#### Step 2.4 — Tests

- A pick with `source="unknown"` and a position increments that team's count at
  that position.
- A pick with `source="unknown"` and no position still consumes a pick number and
  changes nothing else.
- An invalid position raises.
- The board label for an unknown pick shows the position.

**Done when:** you can record an unlisted kicker, see it on the board, and watch
that team's roster count go up.

---

### Phase 3 — Position colours and the draft board

**Goal:** the board is readable at a glance, and its columns line up.

**Files:** `presentation/marks.py` (or a new `presentation/colors.py`),
`presentation/draft_board_view.py`, `pages/draft_runner.py`,
`pages/sim_viewer.py`, tests

#### Step 3.1 — One position palette, shared

Define `POSITION_COLORS` once. Phase 4's bars use the same map, so a running back
is the same colour on the board and in the chart — that consistency is the whole
reason to define it in one place.

Sub-steps:

1. Add a dictionary mapping each of `POSITIONS` to a colour.
2. Use translucent colours (`rgba(...)`) as `MY_TEAM_TINT` in `sim_viewer.py`
   already does, so cells tint in both light and dark themes rather than fighting
   them.
3. Put it wherever `MARK_COLOR_BY_LABEL` lives, so display colours stay together.

*When we build this I will load the `dataviz` skill first — it has a palette
that is checked for colour-blind safety and for contrast in both themes, which
is worth having before picking six colours by hand.*

#### Step 3.2 — Carry position through to the grid

The board grid currently holds strings. Colouring needs to know each cell's
position, and parsing it back out of `"12. Bijan Robinson (RB)"` would be
fragile.

Sub-steps:

1. Change the entries `build_board_grid` consumes from a plain tuple to a
   `NamedTuple` with `pick`, `team`, `label` and `position` fields. A NamedTuple
   still unpacks like a tuple, so this is a small change.
2. Update `entries_from_pick_log` to fill in the position, looking it up
   alongside the label.
3. Add `build_position_grid(entries, config)` returning a same-shaped frame of
   position strings, with empty strings where no pick has landed.
4. Update `sim_viewer.py`'s call, which builds its own entries.

#### Step 3.3 — Colour and size the board

Sub-steps:

1. Build both grids, then `grid.style.apply(...)` with a function that reads the
   position grid and returns a `background-color` per cell.
2. Because the two frames are the same shape, the styling function can index the
   position grid positionally.
3. Give every team column the same fixed pixel width via `column_config`, so the
   snake reads as a grid rather than a ragged one.
4. Keep the `(you)` marker and the keeper `(K)` suffix.

#### Step 3.4 — Tests

- `build_position_grid` puts the right position in the right cell.
- Empty cells give an empty string, not None — `.style` chokes on None.
- Both grids have identical shape and column names.
- `sim_viewer`'s existing board tests still pass.

**Done when:** the board is colour-coded, the columns are even, and a positional
run is visible as a block of colour.

---

### Phase 4 — Cost of waiting as bars

**Goal:** replace the row of numbers with something you can read in a glance.

**Depends on:** Phase 1 (which team's cost) and Phase 3 (the palette).

**Files:** `pages/draft_runner.py`, possibly `presentation/`

#### Step 4.1 — Pick the chart

`st.bar_chart` supports both `horizontal=True` and `color=`, so it can do
length-encoded, position-coloured bars directly. Three options, in order of
effort:

1. **`st.bar_chart`** — least code, gets colour and length. Numbers need a
   separate column or the tooltip.
2. **`st.dataframe` with a `ProgressColumn`** — bar plus number in one row, but
   **no per-row colour**, which is half of what you asked for.
3. **Hand-built HTML bars** — full control, most code.

Recommendation is 1, with the value shown in a small adjacent column so the
number is always visible rather than only on hover.

#### Step 4.2 — Build it

Sub-steps:

1. Take the frame from `positional_costs_for_team`.
2. Sort by cost, most urgent first, so the eye lands on the tallest bar.
3. Map each position to its colour from Phase 3.
4. Render horizontally, with position on one axis and cost on the other.
5. Caption whose decision it describes, carried over from Phase 1.
6. Handle the empty frame — late in the draft there is no "next pick" to wait
   for, and an empty chart should be a caption, not a blank box.

#### Step 4.3 — Check it by eye

There is not much to unit-test in a chart. What matters:

- The bar order matches the sorted frame.
- Colours match the board's for the same position.
- A zero-cost position still shows its label rather than vanishing.

**Done when:** you can tell at a glance which position is urgent, without reading
a number.

---

### Phase 5 — See the board and console together

**Goal:** watch the board fill while working the console.

**Files:** `pages/draft_runner.py`

Sub-steps:

1. Add a third option to the view control: `Both`, `Console`, `Board`, with
   `Both` as the default.
2. In `Both`, render the board first at a capped height so it scrolls internally
   instead of pushing the console off the screen.
3. Keep the single-view options — the full board is worth the whole screen when
   reviewing, and the console is worth it when drafting.
4. Watch the render cost. A `segmented_control` renders only the branch you take,
   which is why it was chosen over `st.tabs`; `Both` deliberately pays for two.
   With autoplay ticking every three seconds, check it still feels responsive.

**Done when:** in Draft Sim you can press Play and watch picks land on the board
without leaving the console.

---

### Phase 6 — Highlight your draft plan

**Goal:** players you planned for this round stand out in the console.

**Files:** `pages/draft_runner.py`, `services/draft_runner_service.py`, tests

This is the fiddliest of the nine, entirely because of the name problem in 2.2.

#### Step 6.1 — Work out the round label

Sub-steps:

1. Take your next pick from `remaining_picks(state)`.
2. Convert it to a round and a position within that round — the same arithmetic
   `DraftPlanService.pick_labels` uses, `f"{round}.{pick_in_round:02d}"`.
3. Reuse `pick_labels` rather than rewriting it, so the two cannot drift.

#### Step 6.2 — Resolve plan names to ids

```python
def planned_canonical_ids(plan, round_label, roster):
    """The canonical ids of players planned for one round, across all positions."""
```

Sub-steps:

1. The plan is keyed by `(round_label, position)`, so collect every entry whose
   round matches, across all four positions.
2. Build a name → canonical id lookup from `roster_service.roster()`, which
   carries both columns.
3. Map the planned names through it.
4. **Count the ones that fail to resolve and surface it** rather than dropping
   them silently — a highlight that is quietly incomplete is worse than none.

#### Step 6.3 — Show it

Sub-steps:

1. Add a `Plan` column to the console grid: a star for planned players, blank
   otherwise.
2. Add it to the `disabled` list so it is read-only.
3. Optionally style it: `st.data_editor` applies a `Styler` **only to
   non-editable columns**, so the marking checkboxes cannot be tinted, but this
   column can.
4. Sorting on it groups your planned players together, which is a free bonus of
   making it a real column.

#### Step 6.4 — Tests

- Players planned for the current round resolve to the right ids.
- A name that resolves to nobody is reported, not silently dropped.
- A player planned for a *different* round is not highlighted.
- An empty plan highlights nobody and does not raise.

**Done when:** with a saved draft plan, your targets for the upcoming round are
visibly marked in the console.

---

## 4. Pitfalls

1. **Filter the data, not the display.** The console's click handler indexes into
   the DataFrame you passed to `st.data_editor`. Both the position filter and the
   search must narrow that frame, and `st.session_state["dr_rows"]` must be set
   from the *same* filtered frame.

2. **`st.data_editor` styles only non-editable columns.** Anything you want
   tinted must be in the `disabled` list.

3. **Team slots are 1-indexed, `snake_order` is 0-indexed.** Phase 1's
   `positional_costs_for_team` and Phase 3's position grid both convert between
   them. This has been the recurring bug in this feature.

4. **Don't parse display strings.** Phase 3 carries position through as data
   rather than reading it back out of `"12. Bijan Robinson (RB)"`.

5. **`.style` needs empty strings, not None**, in the position grid, or the
   styling function raises on unpicked cells.

6. **Re-check the autoplay tick after Phase 5.** Rendering both views doubles the
   work done every three seconds.

---

## 5. Suggested order

1. **Phase 1** — fixes a real wrong number; everything else is presentation.
2. **Phase 2** — removes the live-draft blocker (an unlisted pick you cannot
   record).
3. **Phase 3** — the palette Phase 4 needs.
4. **Phase 4** — the bars.
5. **Phase 5** — layout.
6. **Phase 6** — last, being the fiddliest and the least load-bearing.

Phases 1 and 2 are mostly service-layer work with real tests. Phases 3 to 6 are
mostly Streamlit, so expect to check them in a browser rather than with `pytest`.
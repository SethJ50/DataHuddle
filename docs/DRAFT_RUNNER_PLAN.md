# Draft Runner — Build Plan

A step-by-step guide to building `pages/draft_runner.py` and the code behind it.

Written to be followed by someone who knows Python but not this codebase. Every
step says what to build, why it exists, and how to know it works before moving
on. Code shown is a **skeleton** — signatures, structure, and the parts that are
genuinely easy to get wrong. The bodies are yours to fill in.

---

## 1. What you are building

One page with two modes and two views.

**Modes** (a dropdown at the top):

| Mode | Who makes the picks |
|---|---|
| **Draft Sim** | You pick at your slot. The other 11 teams pick automatically, one every 3 seconds. |
| **Live Draft** | You enter *every* pick, mirroring a real draft happening on another platform. |

**Views** (a toggle):

- **Draft Board** — a grid, rows are rounds, columns are teams. Read-only.
- **Draft Console** — left ¾ a searchable, sortable table of available players;
  right ¼ one team's roster laid out as a starting lineup down to the bench.

### What this page is NOT

It does not *replace* the pre-draft simulation. `scripts/run_draft_sim.py` still
runs the big 10,000-draft simulation offline. This page reuses that run's
**calibrated numbers** and then runs its own small, fast simulations from
whatever the board actually looks like right now.

---

## 2. Concepts you need first

Read this section even if you skim the rest. Four ideas explain the whole design.

### 2.1 The pick log is the only state

The entire draft is stored as **an ordered list of picks**:

```python
[{"pick": 1, "team": 1, "canonical_id": "00-0036322", "source": "user"},
 {"pick": 2, "team": 2, "canonical_id": "00-0034796", "source": "auto"}, ...]
```

Everything else is **derived** — recomputed from that list whenever it is needed:

- who has been drafted (a True/False array, one entry per player)
- how many players each team holds at each position
- which pick is next, and whose turn it is

Never store derived things. This is the same rule `DraftConfig.my_picks` and
`DraftConfig.keeper_picks` already follow: a stored copy can fall out of sync
with the thing it was derived from, and then two parts of the app quietly
disagree. A list of picks cannot disagree with itself.

It also makes undo trivial: **remove the last entry**. Rewinding to pick 30 is
"keep the first 29 entries". No unwinding logic to get wrong.

### 2.2 Re-simulation is cheap, so do it constantly

The pre-draft artifact answers "what happens in a draft where nobody has been
picked yet". The moment pick 1 happens, that assumption is stale.

You might think to filter the saved 10,000 simulations down to the ones that
match what actually happened. **Don't.** After a handful of surprising picks,
zero saved simulations match, and you have nothing left.

Instead, run a *fresh* simulation from the current board every time a pick is
made. Measured on a 250-player pool, 12 teams, 15 rounds:

| Horizon (1000 simulations) | Time |
|---|---|
| To your next pick | 31 ms |
| To the pick after that | 48 ms |
| **Entire rest of the draft** | **165 ms** |

165 ms is nothing. So the plan is: re-simulate the whole remaining draft on every
pick, and get every column you could want for free.

### 2.3 What the runner needs from the saved artifact

Only two things: **`mu` and `sd`** — the calibrated centre and width per player.

Those came out of the calibration loop and are what make the simulation match
reality. Do not use the raw `adp_target`/`stdev_target` instead; they are
uncalibrated, and the draft they produce is subtly the wrong draft.

`DraftBoard` (from `ui_helpers.load_sim_board`) already carries them at
`board.artifact.mu` and `board.artifact.sd`, alongside the player table, VORP,
and the kept-player mask. So the runner builds on `load_sim_board` and does not
load anything itself.

**This means the page requires a saved simulation to work.** If there is none,
show the same warning the Draft Plan page shows and stop.

### 2.4 One board per simulated draft

In Draft Sim mode, the 11 AI teams need opinions. The simulator's rule is that
each manager forms an opinion of every player **once, at the start of a draft**,
and keeps it.

Re-drawing every pick would make managers forget: a player could be nearly taken
at pick 10 and inexplicably survive to pick 60. So draw one board at the start of
the session and step through the draft with it.

The board is a pure function of `(seed, number of teams)`, so you never store it
— recompute it from the session's saved `seed` whenever you need it. That makes
the whole simulated draft reproducible, and it makes rewind-and-replay produce
exactly the same AI picks.

---

## 3. Decisions already made

Do not re-litigate these while building; they were chosen deliberately.

| Question | Decision | Why |
|---|---|---|
| Where state lives | MongoDB, append-only pick log | Survives a refresh mid-draft; undo is a truncate |
| Entering a pick | Click a row in the player table | One click per pick; a live draft moves fast |
| Undo | Rewind to any pick | Free with an append-only log |
| Re-sim horizon | Full rest of draft, every pick | 165 ms buys every column at once |
| AI picks | One board per session, stepped | Matches the model; reproducible from the seed |
| Sessions | One live + many named sims per draft | Practise without endangering the real record |
| Draft Board | Read-only | One write path into state means one place bugs hide |
| Console table | `st.data_editor` | See 3.1 |
| View switching | `st.segmented_control` | See 3.2 |
| Roster slots | Greedy by projection | Simple, good enough, easy to explain |

### 3.1 Why `st.data_editor` and not `st.dataframe`

You want three things from the player table: **sortable** columns, **editable**
marking checkboxes, and **click a row to draft that player**.

- `st.dataframe` supports row selection (`on_select`) but its cells are read-only.
- `st.data_editor` has editable cells but **no `on_select`**.

The way out is a **`ButtonColumn`**: `st.data_editor` supports a column of
buttons with an `on_click` handler. `pages/draft_plan.py` already uses exactly
this for its Move arrows — copy that pattern.

One catch: `st.data_editor` disables column sorting when `num_rows` is
`"dynamic"` or `"add"`. Leave it at the default `"fixed"` and sorting works.

### 3.2 Why `st.segmented_control` and not `st.tabs`

Streamlit renders **every** tab's contents, including hidden ones. With a 3-second
timer firing, you would rebuild the board grid every tick even while looking at
the console. A segmented control plus an `if` renders only what is on screen.

---

## 4. Architecture

```
                    pages/draft_runner.py
                    (thin: mode, view, timer, layout)
                              |
              +---------------+---------------+
              |                               |
   services/draft_runner_service.py    presentation/draft_board_view.py
   - DraftState (pick log + derived)    presentation/roster_view.py
   - make_pick / rewind_to / auto_pick  (pure rendering helpers)
   - live_columns  (the re-sim)
              |
   repositories/draft_session_repo.py        <- Mongo reads/writes
              |
   REUSED, UNCHANGED:
     ui_helpers.load_sim_board  -> table, mu/sd, vorp, kept
     draft_model.engine.sim_batch / monte_carlo_sim
     draft_model.queries.availability_matrix / positional_cost_of_waiting
     draft_model.mechanics.snake_order / picks_for_slot
     services.player_markings_service
```

Note the layering already used everywhere in this repo: **pages are thin**. If
you find yourself writing draft logic inside `draft_runner.py`, it belongs in the
service.

### The cycle, once per pick

```
1. Load the pick log from Mongo
2. Derive: drafted mask, roster counts, current pick, team on the clock
3. If the current pick belongs to a keeper -> record it automatically, go to 1
4. Re-simulate the rest of the draft from that state
5. Turn the result into columns: P(available) at each of your remaining picks,
   cost of waiting per position
6. Render the board / console / rosters
```

Steps 1–3 are pure Python and fully testable without Streamlit. Build them first.

---

## 5. Data model

One document per session. 180 picks in an array is tiny, so a single document is
simpler than one document per pick.

```python
{
  "session_id": "3f9a...",        # uuid4().hex
  "draft_id":   "abc123",         # which league (from DraftService)
  "mode":       "sim",            # "sim" | "live"
  "name":       "Practice 1",     # shown in the session picker; live sessions can be "Live"
  "seed":       20260730,         # AI boards derive from this; makes a sim replayable
  "created_at": "2026-08-03T...",
  "updated_at": "2026-08-03T...",
  "picks": [
    {"pick": 1, "team": 1, "canonical_id": "00-0036322", "source": "user"},
    ...
  ]
}
```

Field notes:

- **`team`** is a draft slot, **1-indexed** (team 1 picks first overall). The
  simulator uses 0-indexed team ids internally. Mixing these up is the single
  easiest mistake in this whole project — `snake_order` returns 0-indexed.
- **`source`** is `"user"`, `"auto"` (AI in sim mode), or `"keeper"`. Used to
  style the board and to know what a rewind should replay.
- **`canonical_id`** is nflreadpy's stable player id, the same key the rest of
  the app joins on.
- **`picks` is append-only.** Only two writes exist: push one pick, or truncate
  to a length. Nothing edits an entry in place.

Add the collection name to `registry.Collections` — never write a bare string.

---

## 6. Build phases

Each phase ends with something you can actually check. Do not start a phase
before the previous one's "done when" is true.

---

### Phase 1 — State, with no user interface

**Goal:** a tested `DraftState` that can answer every question about a draft in
progress, without Streamlit or Mongo being involved.

**Files:** `services/draft_runner_service.py` (new),
`tests/test_draft_runner_state.py` (new)

#### Step 1.1 — Write the `DraftState` class

```python
@dataclass
class DraftState:
    """One draft in progress, as an ordered list of picks."""

    config: DraftConfig          # league shape, keepers, your slot
    picks: list                  # the log; each entry is a dict as in section 5

    # --- derived, recomputed on demand --------------------------------
    @property
    def current_pick(self) -> int: ...        # 1-indexed; total_picks + 1 when finished
    @property
    def on_the_clock(self) -> int: ...        # team slot, 1-indexed
    @property
    def is_complete(self) -> bool: ...
    @property
    def drafted_ids(self) -> set: ...         # canonical_ids already taken
```

Sub-steps:

1. `current_pick` is `len(self.picks) + 1`. That is it — because the log is
   append-only and every pick is recorded, its length *is* the progress.
2. `on_the_clock` calls `snake_order(self.current_pick, config.num_teams,
   config.third_round_reversal)` and **adds 1** to convert 0-indexed to a slot.
3. `is_complete` is `current_pick > config.total_picks`.
4. `drafted_ids` is a set built from the log. A set because the only question
   asked of it is "is this player gone?", which sets answer instantly.

#### Step 1.2 — Derive the arrays the simulator needs

The simulator does not speak in player ids; it speaks in **column indices** into
the player table. Two conversions are needed.

```python
def drafted_mask(self, table) -> np.ndarray:
    """One True/False per table row: has this player been taken?"""

def roster_counts(self, table, pos_index) -> np.ndarray:
    """Shape (1, num_teams, 6): how many at each position each team holds."""
```

Sub-steps:

1. For `drafted_mask`: build it from `table["canonical_id"].isin(self.drafted_ids)`.
2. For `roster_counts`: start with `np.zeros((1, num_teams, len(POSITIONS)), dtype=np.int16)`.
   Walk the log; for each pick find the player's row, then his position number,
   then increment `counts[0, team - 1, position_number]`. **Note the `- 1`** —
   the array is 0-indexed, your log stores 1-indexed slots.
3. Shape `(1, ...)` is deliberate. `monte_carlo_sim` accepts a single row and
   applies it to every simulation, so you do not have to repeat it yourself.

#### Step 1.3 — Operations

```python
def make_pick(self, canonical_id, source="user") -> None: ...
def rewind_to(self, pick_number) -> None: ...     # keep picks before pick_number
def apply_keeper_if_due(self) -> bool: ...        # returns True if it recorded one
```

Sub-steps for `make_pick`:

1. Reject a player already in `drafted_ids` — loudly. A duplicate would put one
   person on two rosters and every count downstream would be wrong.
2. Reject a pick when `is_complete`.
3. Append `{"pick": current_pick, "team": on_the_clock, "canonical_id": ..., "source": ...}`.

Sub-steps for `apply_keeper_if_due`:

1. Look up `self.config.keeper_picks` — a dict of `{overall_pick: canonical_id}`.
2. If `current_pick` is a key, call `make_pick(that player, source="keeper")` and
   return True.
3. The caller loops on this until it returns False, since two keepers can sit on
   consecutive picks.

#### Step 1.4 — Tests

Write these before moving on. They need no database and no Streamlit.

- An empty state is on pick 1, and team 1 is on the clock.
- After N picks, `current_pick` is N+1 and the team matches `snake_order`.
- Round 2 runs backwards (a snake check through the state, not just mechanics).
- Drafting the same player twice raises.
- `rewind_to(k)` leaves exactly k-1 picks, and `current_pick` becomes k.
- A keeper at pick 7 is auto-recorded when the draft reaches pick 7, with
  `source="keeper"`.
- `roster_counts` matches a hand-counted example — **specifically check that a
  pick by team 1 lands in index 0**.

**Done when:** `pytest tests/test_draft_runner_state.py` passes and you have not
imported streamlit or pymongo in the service.

---

### Phase 2 — Persistence

**Goal:** sessions survive a browser refresh.

**Files:** `repositories/draft_session_repo.py` (new), `registry.py` (edit)

#### Step 2.1 — Add the collection name

In `registry.Collections`, add `DRAFT_SESSIONS = "draft_sessions"`.

#### Step 2.2 — Write the repository

Follow the shape of `repositories/adp_snapshot_repo.py` — it is the closest
existing example of a repository that queries live rather than caching.

```python
class DraftSessionRepo:
    def create(self, draft_id, mode, name, seed) -> dict: ...
    def get(self, session_id) -> dict | None: ...
    def list_for_draft(self, draft_id) -> list: ...
    def append_pick(self, session_id, pick: dict) -> None: ...
    def truncate_to(self, session_id, keep: int) -> None: ...
    def delete(self, session_id) -> None: ...
```

Sub-steps:

1. Use the helpers in `db/documents.py` (`find_one`, `find_all`, `upsert`,
   `delete`). Do not import pymongo here.
2. `append_pick` should use MongoDB's `$push`, and `truncate_to` a `$slice`.
   Rewriting the whole array each time also works and is simpler to start with —
   180 small entries is nothing. Optimise later only if it feels slow.
3. Always stamp `updated_at`.
4. Add an index on `draft_id` via `ensure_index` so the session picker is fast.

#### Step 2.3 — Tests

Point the repo at a throwaway collection name so tests never touch real data —
`tests/test_keeper_board.py` shows the pattern of injecting a stub.

**Done when:** you can create a session, append three picks, reload it by id, and
get the same three picks back.

---

### Phase 3 — The live re-simulation

**Goal:** given a `DraftState`, produce the numbers the console will show.

**Files:** `services/draft_runner_service.py` (extend),
`tests/test_draft_runner_live.py` (new)

#### Step 3.1 — The re-sim call

```python
def resimulate(state, board, n_sims=1000):
    """Simulate the rest of the draft from where it actually stands."""
    picks = monte_carlo_sim(
        board.artifact.mu,                 # CALIBRATED, not adp_target
        board.artifact.sd,
        position_index(board.table["position"]),
        state.config,
        n_sims=n_sims,
        start_pick=state.current_pick,
        end_pick=state.config.total_picks,
        already_drafted=state.drafted_mask(board.table),
        roster_counts=state.roster_counts(board.table, pos_index),
        keeper_picks=DraftSimService.keeper_columns(state.config, board.table),
    )
    return picks
```

Sub-steps and cautions:

1. Pass **the full** `keeper_picks` dict every time, including keepers already
   reached. The loop only walks `start_pick` to `end_pick`, so past ones are
   skipped naturally, and future ones stay correctly reserved.
2. `already_drafted` marks players *taken*; `keeper_picks` reserves the *pick*.
   Both are needed and they do different jobs.
3. Do **not** pass `rng`. Leaving it out makes the run derive from
   `config.random_seed`, so the same board state always gives the same numbers —
   which stops the screen flickering with noise between reruns.

#### Step 3.2 — Turn the matrix into columns

```python
def live_columns(state, board, picks) -> pd.DataFrame:
    """One row per still-available player, with the numbers worth showing."""
```

Sub-steps:

1. Work out which of your picks are still ahead: the entries of
   `config.my_selectable_picks` greater than or equal to `state.current_pick`.
   (`my_selectable_picks`, not `my_picks` — it already excludes a pick spent on
   your own keeper.)
2. Call `availability_matrix(picks, those_picks)` for the whole grid at once.
3. Build a DataFrame from `board.table` with `name`, `position`, `projection`,
   `tier`, `adp_target`, plus one `P@<pick>` column per remaining pick.
4. Drop rows where `state.drafted_mask` is True — the table shows only who is
   *available*.
5. Also drop rows where `board.kept` is True. A kept player is on someone's
   roster before the draft opens and can never be selected.

#### Step 3.3 — Positional cost of waiting

For each position, call `positional_cost_of_waiting(picks, position, at_pick,
my_next_pick, board.vorp, positions)` where `at_pick` is your next pick and
`my_next_pick` the one after.

You get this free from the full-horizon re-sim. Show it above the console table.

#### Step 3.4 — Cache it

Re-simulating on every Streamlit rerun (not just every pick) would be wasteful —
Streamlit reruns on *any* widget interaction, including typing in the search box.

```python
@st.cache_data(show_spinner=False)
def cached_resim(_board, session_id: str, pick_count: int, n_sims: int):
    ...
```

Sub-steps:

1. The leading underscore on `_board` tells Streamlit not to hash it — it holds
   numpy arrays and is expensive to hash. The same trick is used in
   `ui_helpers._cached_board`.
2. `pick_count` is the cache key that matters. Because the log is append-only,
   its **length uniquely identifies the state** — a neat consequence of the
   design in 2.1.
3. `session_id` keeps two sessions from sharing an entry.

#### Step 3.5 — Tests

- With an empty log, availability at pick 1 is 1.0 for everyone (nobody is gone).
- After drafting player X, X is absent from `live_columns`.
- A player is never available at a pick before one where he is certain to be
  gone — probabilities should not increase as picks get later.
- Kept players never appear.

**Done when:** you can print the top 20 available players with sensible
probabilities from a hand-built pick log.

---

### Phase 4 — The Draft Board view

**Goal:** the rounds × teams grid, working for both modes.

**Files:** `presentation/draft_board_view.py` (new), `pages/sim_viewer.py` (edit)

#### Step 4.1 — Lift the existing grid builder

`pages/sim_viewer.py` already has `build_sim_board`, which builds exactly this
grid from a simulated draft. Move that logic into
`presentation/draft_board_view.py` and change its input from a picks-matrix row
to a **pick log**, which both callers can produce.

Sub-steps:

1. New signature: `build_board_grid(picks_log, config, name_by_id)`.
2. For each entry: `round_index = (pick - 1) // num_teams`, and the column is the
   team slot minus 1.
3. Return a DataFrame with rounds as the index (`R1`, `R2`, …) and one column per
   team, your own column labelled `(you)`.
4. Update `sim_viewer.py` to convert its matrix row into a small pick log and
   call the shared function. Its tests must still pass.

Why bother sharing: two functions drawing "the draft board" will drift, and the
day they disagree you will not know which is right.

#### Step 4.2 — Style it

- Mark keeper picks `(K)`.
- Tint your own column (`sim_viewer.py` has `MY_TEAM_TINT` — reuse it).
- Highlight the most recent pick so your eye finds the front of the draft.

**Done when:** a 12×15 board renders, reads left-to-right in odd rounds and
right-to-left in even ones, and the Sim Viewer page still works.

---

### Phase 5 — The Draft Console → Live mode complete

**Goal:** you can run a whole live draft. This is the phase that makes the page
useful; Sim mode is a bonus on top.

**Files:** `pages/draft_runner.py`, `presentation/roster_view.py` (new)

#### Step 5.1 — Layout

```python
console, rosters = st.columns([3, 1])
```

#### Step 5.2 — The player table

Inside `console`:

1. A `st.text_input` for search. Filter the DataFrame *before* passing it in —
   do not try to make the table search itself.
2. `st.data_editor` with:
   - a **`ButtonColumn`** named `Draft`, `on_click` calling your pick handler
     (copy the `Move` column in `draft_plan.py`)
   - read-only columns: Player, Pos, Proj, Tier, ADP, `P@<your next pick>`, Cost
   - editable checkbox columns for markings, from `MARKING_CATEGORIES`
   - `num_rows="fixed"` so sorting stays enabled
3. **Give the editor a key that changes every pick**: `key=f"console_{len(state.picks)}"`.
   This matters. `st.data_editor` tracks edits by row position, and your row set
   shrinks by one every pick — a stale key applies a checkbox edit to the wrong
   player. A fresh key each pick avoids that entirely, at the cost of needing to
   commit marking edits on the same rerun rather than letting them accumulate.

#### Step 5.3 — The pick handler

```python
def draft_player(canonical_id):
    state.make_pick(canonical_id, source="user")
    repo.append_pick(session_id, state.picks[-1])
    while state.apply_keeper_if_due():
        repo.append_pick(session_id, state.picks[-1])
```

Sub-steps:

1. Update in-memory state and Mongo together, so a refresh cannot lose a pick.
2. Loop the keeper check — consecutive keeper picks are possible.
3. Streamlit reruns automatically after an `on_click` handler, so do not call
   `st.rerun()` here.

#### Step 5.4 — Undo and rewind

- **Undo**: a button calling `rewind_to(state.current_pick - 1)`.
- **Rewind**: a number input plus a Go button, for jumping back several rounds.
- Show a confirmation for a rewind that discards more than a few picks.

#### Step 5.5 — The roster panel

`presentation/roster_view.py`:

```python
def slot_roster(players, starting_slots) -> list:
    """Assign a team's players to QB / RB / RB / WR / ... / BN."""
```

Sub-steps (greedy, in this order):

1. Sort the team's players by projection, best first.
2. Fill each **dedicated** slot from `starting_slots` (QB, RB, WR, TE, K, DST)
   with the best unused player at that position.
3. Fill **FLEX** slots from the best unused player among RB, WR, TE.
4. Everything left over becomes bench (`BN`).
5. Return a list of `(slot_label, player_or_None)` so empty slots still render —
   seeing an unfilled RB2 is the point of the panel.

Above it, a `st.selectbox` of teams, defaulting to yours.

**Done when:** you can run a complete live draft from pick 1 to the end, refresh
the browser mid-draft and lose nothing, and undo a mistyped pick.

---

### Phase 6 — Sim mode: AI picks and autoplay

**Goal:** the other 11 teams pick themselves, every 3 seconds.

**Files:** `services/draft_runner_service.py` (extend), `pages/draft_runner.py`

#### Step 6.1 — Draw the session's board

```python
def session_board(state, board):
    """The one board this simulated draft's AI managers use."""
    return draw_boards_for_sims(
        board.artifact.mu, board.artifact.sd,
        state.config.num_teams,
        seed=state.seed,        # from the session document
        sim_indices=[0],
    )                            # shape (1, n_players, num_teams)
```

Recompute this whenever you need it. It is fast and deterministic, so storing it
buys nothing and risks it going stale.

#### Step 6.2 — Step one pick

```python
def auto_pick(state, board, session_board_array):
    """Work out who the team on the clock takes, and record it."""
    picks = sim_batch(
        session_board_array, pos_index, state.config,
        start_pick=state.current_pick,
        end_pick=state.current_pick,          # exactly one pick
        already_drafted=state.drafted_mask(board.table)[None, :],
        roster_counts=state.roster_counts(board.table, pos_index),
        keeper_picks=...,
    )
    column = int(np.flatnonzero(picks[0] == state.current_pick)[0])
    state.make_pick(board.table.iloc[column]["canonical_id"], source="auto")
```

Sub-steps:

1. `start_pick == end_pick` runs exactly one pick.
2. The returned matrix has the chosen player marked with that pick number; find
   the column holding it.
3. Because the board came from a fixed seed and the state is the full history,
   this is **deterministic** — rewinding and replaying gives the same AI picks.

#### Step 6.3 — Autoplay

```python
@st.fragment(run_every="3s" if playing else None)
def autoplay_tick():
    if not playing or state.is_complete:
        return
    if state.on_the_clock == config.draft_position:
        return                     # your turn: stop and wait
    auto_pick(...)
    repo.append_pick(...)
    st.rerun(scope="app")          # refresh board and rosters, not just this fragment
```

Sub-steps and cautions:

1. A **fragment** is a piece of the page Streamlit can rerun on its own.
   `run_every="3s"` makes it tick.
2. Passing `run_every=None` when paused stops the timer entirely, rather than
   ticking and doing nothing.
3. `st.rerun(scope="app")` is essential. Without the scope the fragment refreshes
   *itself* and the board and rosters keep showing the previous pick.
4. Guard on `is_complete` or it will keep firing forever at the end of the draft.

#### Step 6.4 — Controls

- **Pause / Play** — a toggle in `st.session_state`.
- **Step** — advance exactly one AI pick, ignoring the timer.
- **To my pick** — loop `auto_pick` until it is your turn. Do this in one rerun
  rather than 3 seconds each.

**Done when:** a simulated draft runs start to finish, pauses at each of your
picks, and rewinding then replaying reproduces the same AI picks exactly.

---

### Phase 7 — Session management

**Goal:** one live session per draft, plus named practice sims.

Sub-steps:

1. A `st.selectbox` listing this draft's sessions, plus "New sim…".
2. Creating a sim asks for a name and generates a fresh `seed` — a different seed
   means a genuinely different draft to practise against.
3. The live session is created once and always resumable; never offer to delete
   it without a confirmation.
4. Delete for sim sessions.

**Done when:** you can keep three named practice sims and one live draft on the
same league without them interfering.

---

## 7. API reference

Exact signatures for what you will call. All verified against the current code.

```python
# draft_model/engine.py
sim_batch(boards, pos_index, config, *, start_pick=1, end_pick=None,
          already_drafted=None, roster_counts=None, keeper_picks=None) -> np.ndarray
monte_carlo_sim(mu, sd, pos_index, config, n_sims=10000, rng=None,
                batch_size=250, rho=0.35, **kwargs) -> np.ndarray
draw_boards_for_sims(mu, sd, num_teams, seed, sim_indices, rho=0.35) -> np.ndarray
position_index(positions) -> np.ndarray

# draft_model/queries.py
availability_matrix(picks, target_picks) -> np.ndarray        # (n_players, n_target_picks)
positional_cost_of_waiting(picks, position, at_pick, my_next_pick,
                           vorp, positions, available_mask=None) -> float

# draft_model/mechanics.py
snake_order(pick_num, num_teams, third_round_reversal=False) -> int   # 0-INDEXED team
picks_for_slot(draft_position, num_teams, num_rounds,
               third_round_reversal=False) -> tuple                   # 1-indexed slot in

# draft_model/config.py  (DraftConfig)
.total_picks .my_picks .my_selectable_picks .keeper_picks .kept_player_ids

# services/draft_sim_service.py
DraftSimService.keeper_columns(config, table) -> dict     # {overall_pick: column index}
DraftBoard fields: config, table, artifact, vorp, replacement, stale, kept
  artifact.mu / artifact.sd   <- the calibrated numbers you simulate with

# ui_helpers.py
load_sim_board(ctx, draft, year=2026) -> (DraftBoard | None, error | None)

# services/player_markings_service.py
.get(draft_id, canonical_id) / .save(draft_id, canonical_id, categories, notes)
/ .all_for_draft(draft_id)
```

---

## 8. Pitfalls

Ordered roughly by how likely they are to bite.

1. **Team indexing.** `snake_order` returns **0-indexed** team ids; the pick log
   stores **1-indexed** slots, and `picks_for_slot` takes a 1-indexed slot. Every
   conversion needs a `+ 1` or `- 1`. Write a test that a pick by team 1 lands in
   array index 0.

2. **`st.data_editor` keys.** Covered in 5.2. The available list shrinks every
   pick; a stale key mis-applies checkbox edits.

3. **`st.rerun(scope="app")` inside a fragment.** Without the scope argument only
   the fragment refreshes and the rest of the page shows stale data.

4. **Calibrated vs raw numbers.** Simulate with `artifact.mu` / `artifact.sd`.
   Using `adp_target` / `stdev_target` runs an uncalibrated draft that looks
   plausible and is wrong.

5. **Keepers need both treatments.** `already_drafted` takes the *player* off the
   board; `keeper_picks` reserves the *pick*. Doing only one gives a draft with
   the wrong number of selections.

6. **Do not cache on the state object.** Cache on `(session_id, len(picks))`.
   Streamlit cannot hash a DataFrame or a numpy array cheaply, and a mutable
   object as a cache key is a bug waiting to happen.

7. **A missing simulation is normal.** If `load_sim_board` returns an error,
   render the same warning the Draft Plan page does and stop. Do not fall back to
   uncalibrated numbers silently.

8. **Sorting the player table does not reorder your data.** `st.data_editor`
   sorting is a display concern; your `on_click` handler receives the row index
   into the DataFrame **you passed in**. Look the player up by id, not position.

---

## 9. Suggested order of work

If you want the shortest path to something usable:

1. **Phase 1** (state) — pure Python, all tests, no UI. Half the work, none of the
   frustration.
2. **Phase 3** (re-sim) — still no UI. Print results to a terminal and sanity-check
   the probabilities by eye.
3. **Phase 2** (persistence) — small, mechanical.
4. **Phase 5 + 4** (console, board) — **Live mode now works end to end.** Stop here
   if the season is close; this is the part that matters on draft day.
5. **Phase 6** (sim mode) — the fiddliest Streamlit work. Leave it last, because
   fragments and timers need a real browser to shake out.
6. **Phase 7** (sessions) — polish.

Phases 1–3 are roughly two-thirds of the logic and can be built and tested
entirely with `pytest`. Do them first and the UI phases become assembly.
# The Draft Simulation System — Developer Onboarding

*A guide to how DataHuddle simulates fantasy football drafts, written for someone
new to the codebase. Read top to bottom; each section builds on the one before it.*

---

## Table of contents

1. [What problem this solves](#1-what-problem-this-solves)
2. [The idea in one page](#2-the-idea-in-one-page)
3. [Layer 1 — Where the numbers come from](#3-layer-1--where-the-numbers-come-from)
4. [Layer 2 — The model table](#4-layer-2--the-model-table)
5. [Layer 3 — Draft mechanics](#5-layer-3--draft-mechanics)
6. [Layer 4 — The simulator](#6-layer-4--the-simulator)
7. [Layer 5 — Calibration](#7-layer-5--calibration)
8. [Layer 6 — Saving results](#8-layer-6--saving-results)
9. [Layer 7 — Asking questions](#9-layer-7--asking-questions)
10. [Layer 8 — Reaching the app](#10-layer-8--reaching-the-app)
11. [How to run everything](#11-how-to-run-everything)
12. [Invariants: the rules you must not break](#12-invariants-the-rules-you-must-not-break)
13. [What is deliberately not built](#13-what-is-deliberately-not-built)

---

## 1. What problem this solves

### The fantasy football situation

In a fantasy football draft, twelve (or ten, or fourteen) people take turns picking
real NFL players. You get one pick, then wait for everyone else, then pick again.
In a **snake draft** the order reverses each round: if you pick 4th in round 1, you
pick 4th-from-last in round 2, and so on.

The whole game is played in the gap between your picks. You're never really choosing
"who is the best player available" — you're choosing between:

> *"Take this running back now, or take a wide receiver and hope a comparable
> running back is still there in fifteen picks?"*

Answering that requires knowing **who will still be available when you pick again**.

### Why existing data isn't enough

Fantasy sites publish **ADP** — Average Draft Position. If a player's ADP is 12.4,
he goes around the 12th pick on average. That sounds like it should answer the
question, but it doesn't:

- ADP is an *average*. A player with ADP 12 might go anywhere from 6 to 25.
- ADP tells you nothing about **combinations**. "Will at least one of these four
  running backs last until pick 29?" cannot be computed from four separate averages,
  because those four players compete for the same picks — if one goes early, that's
  precisely what lets another survive.
- ADP describes *the market*, not *your league*. Your leaguemates draft on a
  specific platform, and that platform's default player list anchors their behaviour.

### What this system does instead

It **plays out your draft ten thousand times** with simulated opponents, then counts
what happened. If Bijan Robinson was still on the board at pick 29 in 3,000 of 10,000
simulated drafts, that's a 30% chance.

Counting is the whole trick. Once you have ten thousand complete drafts recorded, any
question becomes arithmetic — including the combination questions that no closed-form
formula can answer.

---

## 2. The idea in one page

Here is the entire pipeline. Every later section expands one box.

```
  REAL-WORLD DATA                    Fantasy Football Calculator  (ADP + spread)
        │                            ESPN / Yahoo / Sleeper       (ADP)
        │                            Fantasy Footballers          (projections)
        ▼
  ┌──────────────────┐
  │  MODEL TABLE     │   one row per player:  adp_target, stdev_target, projection
  │  table.py        │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  CALIBRATION     │   adjust the inputs until the simulation reproduces the ADP
  │  calibrate.py    │   it was given.  Output: mu, sd
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  SIMULATOR       │   10,000 fake drafts.  Output: a big grid of pick numbers
  │  engine.py       │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  ARTIFACT (.npz) │   saved to disk so the app never re-simulates
  │  artifacts.py    │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  QUERIES         │   "how often was he still there at pick 29?"  → count, divide
  │  queries.py      │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  DRAFT PLAN PAGE │   availability bars, cost of waiting, tier survival
  └──────────────────┘
```

### The two axes, kept deliberately separate

This distinction runs through the whole system and confusing the two is the most
common way this kind of model goes quietly wrong:

| | Answers | Comes from |
|---|---|---|
| **Timing axis** | *When* does a player get drafted? | ADP and its spread |
| **Value axis** | *What is he worth* if you get him? | Projections |

Availability needs only the first. "Does it matter?" needs both.

### Where the code lives

```
draft_model/          the numerical core — pure numpy/pandas, no database, no UI
  config.py             league settings and every tunable constant
  mechanics.py          whose pick is it; how positional need distorts a board
  table.py              assembling one row per player from messy vendor data
  engine.py             the simulator itself
  calibrate.py          making the simulator reproduce reality; validating it
  artifacts.py          saving and loading results
  queries.py            turning a grid of pick numbers into answers

adapters/             vendor data → clean shapes (one file per source's quirks)
repositories/         database reads and writes
services/             business logic; wires the core to the app's data
scripts/              things you run from the terminal
pages/                the Streamlit UI
tests/                105 tests
```

`draft_model/` deliberately has **no dependency on the database or Streamlit**. That
is what lets it be tested in isolation and reasoned about on its own.

---

## 3. Layer 1 — Where the numbers come from

The simulator needs exactly three things per player: **when he typically goes**,
**how much that varies**, and **what he's worth**.

### 3.1 Fantasy Football Calculator — the only source of spread

**Why it's special:** almost nobody publishes how much a player's draft position
*varies*. FFC does, as a `stdev` column, and that number is what makes probabilistic
answers possible at all. Everything else in the system has alternatives; this doesn't.

`adapters/ffc_adapter.py` handles it:

- **`FfcAdapter.fetch(fmt, year, teams)`** — one HTTP call, returns an `FfcPull`.
- **`normalize_players(raw)`** — vendor JSON → clean DataFrame.
- **`FfcPull`** — a small result object carrying `.ok`, `.players`, `.meta`, `.error`.

Three findings from probing the live API that the code depends on:

1. **The `teams` parameter is ignored.** ADP is byte-identical for 8, 10, 12 and 14
   teams. We still send a valid value (13 returns an error) but it selects nothing.
2. **There are two different ways to get no data** — an HTTP 400 for an out-of-range
   year, *and* an HTTP 200 with an empty list for a year FFC simply has nothing for
   (2025 is such a hole, sitting between populated years). `FfcPull.ok` collapses
   both into one flag, so a loop over years can't accidentally handle only one.
3. **Positions come back as `PK` and `DEF`**, not `K` and `DST`.

**Sentinel handling.** A `stdev` of exactly `0.0` occurs — one 2026 player was drafted
five times at precisely pick 177. Zero doesn't mean "no spread", it means "we couldn't
measure spread". The adapter converts it to `NaN` so downstream code is forced to
fall back explicitly rather than treating him as perfectly predictable.

### 3.2 Platform ADP — ESPN, Yahoo, Sleeper

`adapters/adp_source_adapter.py`. These come from CSV files you scrape by hand into
`data/`, then load into MongoDB.

**The sentinel trap, and why it matters.** Sleeper writes `999` for players it has no
ADP for. Left alone, that value averages with the other platforms — a player reading
ESPN 169 / Yahoo 125 / Sleeper 999 blends to about 355, which is nonsense. Before this
was guarded, **71 players had a blended ADP roughly 409 picks too deep and were being
silently dropped from the simulation entirely.**

`_drop_sentinels()` converts anything at or beyond `MAX_PLAUSIBLE_ADP` (500) to `NaN`.
Even a 20-team, 25-round draft is only 500 picks, so no real ADP can reach it.

### 3.3 Fantasy Footballers projections — the value axis

Three analysts (Andy, Mike, Jason) publish separate projections. `services/projections_service.py`:

- **`get_own_projections()`** — the blend of all three (the default).
- **`get_own_projections("mike")`** — one analyst.
- **`disagreement(fmt)`** — where the three disagree most.

The blend is a plain average, because averaging independent forecasts reliably beats
picking one. Two details:

- The average is over **whoever actually rated each player**, not over three. The
  analysts cover slightly different pools, and treating a missing projection as zero
  would bury deep players.
- Scoring is a *linear* function of the stat line, so averaging their points gives
  exactly the same answer as averaging their stats and scoring once.

**The disagreement signal** is a genuinely new measurement. Where the three split on
a player, that's forecasting uncertainty *independent of ADP spread*:

```
Tua Tagovailoa       87 → 231 points   (spread 144)
Michael Penix Jr.     0 → 143 points   (spread 143)
```

Both are players whose role is unsettled. A player everyone projects identically but
the market can't price is a very different bet from one the analysts themselves split
on. Nothing consumes this yet — it's available when you want it.

### 3.4 Player identity — the join problem

Every source spells names differently. `repositories/player_directory.py` handles it:

- **`normalize_name(name)`** — strips accents, punctuation, and generational suffixes.
  `"James Cook III"` → `"james cook"`, `"Eddy Piñeiro"` → `"eddy pineiro"`.
- **`resolve_by_display_name(names, positions)`** — two passes: exact match first
  (unchanged behaviour), then normalized matching **only for names that already
  failed**. Strictly additive, so no previously-correct match can shift.

**Ambiguity resolves to nothing, never to a guess.** If two different players share a
normalized name *and* position — there are two Adrian Petersons, both RB — that key is
excluded and both stay unresolved. Leaving a player unmatched is visible and fixable;
silently attaching the wrong ID is an error that never surfaces.

> **Why this mattered.** Before normalization existed, ten UDK-ranked players were
> being silently dropped from the app's entire player universe — including
> **James Cook III, a round-1 running back.** `RosterService.roster()` discards rows
> without an ID, so he simply didn't exist anywhere in DataHuddle.

Two cases normalization can't reach get manual rows in `data/player_id_map.csv`:
**Travis Hunter** (nflreadpy lists him at CB — he's a genuine two-way player — while
every fantasy source says WR) and **Hollywood Brown** (a nickname for Marquise Brown).

### 3.5 Storage

Two collection styles, never mixed:

| Style | Collections | Lifecycle |
|---|---|---|
| **Current state** | `ffc_adp`, `espn_projections`, `udk_*_rankings_ppr`, … | Wiped and replaced on every `load_data` run |
| **History** | `adp_snapshots` | Append-only, never overwritten |

`repositories/adp_snapshot_repo.py` manages the history. It exists purely as
**insurance**: FFC is a free API with no contract, and if it disappears, the archived
history is the only raw material left for estimating spread another way. Nothing reads
it today.

Two mechanisms worth knowing:

- **`compute_content_hash()`** — fingerprints a payload so an unchanged one is skipped.
  Without it, snapshotting an un-refreshed CSV weekly would store identical rows and
  make computed volatility read as exactly `0.0` — which looks like a confident
  measurement rather than missing data.
- **`ensure_indexes()`** — a *unique* index on the key, so a duplicate is rejected by
  the database rather than silently doubling rows.

---

## 4. Layer 2 — The model table

`draft_model/table.py` turns messy vendor data into **one flat table, one row per
player**. This is the boundary: nothing downstream ever touches a vendor field.

### The functions

**`blend_adp(sources, weights)`** — combines ESPN/Yahoo/Sleeper into one number.

The subtle part is that **weights are renormalized per player**. If Sleeper has weight
0.2 and a deep player appears *only* in Sleeper, he should get Sleeper's ADP — not
Sleeper's ADP scaled down to a fifth of it. A plain weighted sum gets this wrong.

**`apply_platform_shift(ffc_adp, platform_adp, weight)`**:

```
adp_target = ffc_adp + weight × (platform_adp − ffc_adp)
```

Why shift rather than just use platform ADP? FFC is the only source of `stdev`, and its
spread describes variation *within FFC's own drafts around FFC's own ADP*. Pairing that
width with a completely different centre mixes two populations. Shifting keeps one
coherent base and makes the platform anchor an explicit, tunable adjustment.

**`fill_missing_stdev(df)`** — every player needs a usable width. A three-step chain,
with **no trained model anywhere in it**:

1. FFC's `stdev`, whenever present. The overwhelming majority.
2. `(high − low) / 4`. A bell-shaped spread spans about four standard deviations.
3. **Median `stdev` of the 20 nearest-ADP players at the same position.**

Step 3 is defensible rather than a guess because spread rises smoothly with ADP
(median 2.65 → 6.0 → 10.35 → 15.15 across ADP bands). Same-position because QB and TE
spread differently from RB and WR. Measured: exactly **one player in 246** needs it.

**`build_table(config, ffc, ...)`** — assembles everything and returns the final table:

```
ffc_player_id | canonical_id | name | position | team
              | adp_target | stdev_target | times_drafted | projection | mu | sd
```

Two things about it:

- **Keyed by `ffc_player_id`, not `canonical_id`.** The simulator needs only ADP,
  spread and position — not identity. Team defenses can never resolve to an nflreadpy
  ID, and dropping them would push ~27 skill players artificially later, since defenses
  really do get drafted. So `canonical_id` rides along as a *nullable* join column for
  the display layer and is **never used as a filter**.
- **Validation runs before the pool cap.** In pandas, `NaN <= 270` is `False`, so a
  player with a missing ADP would be silently discarded by the cap rather than
  reported. Order matters.

---

## 5. Layer 3 — Draft mechanics

`draft_model/mechanics.py`. Small, and disproportionately dangerous.

**`snake_order(pick_num, num_teams, third_round_reversal)`** — which team owns a pick.

Odd rounds run forward (0, 1, 2, …), even rounds backward. Third-round reversal (where
round 3 repeats round 2's order) is handled by pretending the round number is one
higher from round 3 on, which flips the parity for everything after.

> **Why this function has the most tests in the project.** An off-by-one here does not
> crash. It produces a completely plausible draft in which the wrong teams own the
> picks, and every probability built on top is quietly wrong. Beyond unit tests, the
> formula was verified against an independently-constructed order (literally
> alternating a list) across 20 combinations of league size, rounds and reversal mode.

**`picks_for_slot(draft_position, num_teams, num_rounds)`** — every pick you own.
Derived *from* `snake_order` rather than given its own arithmetic, so the two can't
drift apart. For slot 5 of 12 it returns `(5, 20, 29, 44, …)` — note the gaps alternate
15, 9, 15, 9, which is the snake turning around.

**`effective_value(base_value, position, roster_counts, pick_num)`** — how a manager's
own roster distorts his board. Two rules:

- Position already full → add `BLOCK` (10,000), making him effectively unpickable.
- No starter there yet and it's getting late → subtract `NEED_BONUS` (15), i.e. reach.

**Positional runs are an emergent property of these two rules**, not something
programmed anywhere. Once two managers take tight ends, the remaining TE-less managers
start reaching, and the clustering appears on its own.

This is the **scalar reference implementation** — slow, obviously correct, and used
only by tests to prove the fast vectorized path agrees with it.

---

## 6. Layer 4 — The simulator

`draft_model/engine.py`. The heart of the system.

### 6.1 What one simulated draft looks like

1. Each of the N simulated managers forms a private opinion of every player — a
   **board value**. Lower is better.
2. For each pick in order, the team on the clock takes the available player with the
   lowest adjusted value on *their* board.
3. Record the pick number each player went at. Anyone never taken gets `999`.

**Boards are drawn once per draft, never per pick.** Re-drawing mid-draft would make
each pick forget what the last one believed, so a player could be nearly taken at pick
10 and inexplicably survive to pick 60. Drawing up front gives each simulated draft a
coherent personality.

### 6.2 How opinions are drawn

**`draw_boards(mu, sd, num_teams, rng, n_sims, rho)`**:

```python
value = mu + sd × (rho × shared_shock + √(1 − rho²) × private_noise)
```

- `shared_shock` — one draft-wide surprise per player, felt by every manager.
- `private_noise` — each manager's individual deviation.
- `rho` — how much the managers **agree with each other within one draft**.

The coefficients are chosen so total variance stays exactly `sd²` for *any* `rho`.
Each manager's marginal opinion is always `N(mu, sd)`; `rho` only changes how much they
agree. `rho = 0` reproduces fully independent managers exactly.

> **`rho` cannot be fitted, and the code says so.** It's confounded with `sd` — any
> value produces a fit, because calibration simply refits `sd` around it. It's set to
> a documented judgment value (0.35). Building a loop that appeared to fit it would
> produce a confident-looking number that means nothing.

### 6.3 The performance idea

The obvious implementation runs one draft as a Python loop over 150 picks, then repeats
it 10,000 times — 1.5 million interpreted iterations. Slow enough that people start
trading away correctness to rescue it (pre-sorting boards, only considering the next 15
players).

**None of that is necessary**, because of one observation:

> Snake order is deterministic. At pick 47, **team 3 is on the clock in every single
> simulation.**

So all simulations can advance one pick **together**, as a single numpy operation:

```python
for pick in range(start_pick, end_pick + 1):
    team = snake_order(pick, num_teams)         # same team in every simulation

    values = boards[:, :, team].copy()           # (n_sims, n_players)
    values += BLOCK * (roster_full)              # applied at READ time
    values -= NEED_BONUS * (needs_a_starter)
    values[taken] = np.inf

    choice = values.argmin(axis=1)               # every simulation picks at once
```

The loop runs 150 times, not 1.5 million. **Measured: 0.19 ms per draft — about
2 seconds for 10,000 drafts** on a 246-player pool.

**`sim_batch(boards, pos_index, config, ...)`** runs this. It takes *pre-drawn* boards
as an argument rather than drawing them internally, which is what makes it possible to
hand the same boards to the slow reference implementation and demand *byte-identical*
drafts — not merely similar statistics.

**`monte_carlo_sim(mu, sd, pos_index, config, n_sims)`** batches over simulations to
cap memory. Boards are `(n_sims, n_players, num_teams)` floats; at 10,000 that would be
144 GB, while a batch of 250 is about 4 MB.

**`draw_boards_for_sims(...)`** gives simulation *s* its own random stream keyed by
`(seed, s)`.

> **Why that exists.** Originally a single shared generator handed out numbers in
> request order, so regrouping the same simulations into different batch sizes assigned
> different random numbers to different players. `batch_size` — a pure memory-tuning
> knob — silently changed results. Someone lowering it to fit a smaller machine would
> have changed their draft advice with no indication. Now any single simulation can
> also be regenerated in isolation, which is how you investigate "why did simulation
> 4,732 do that?" without re-running the other 9,999.

### 6.4 Output

An `(n_sims, n_players)` grid of 16-bit integers. `picks[s, i]` is the pick number
player `i` went at in simulation `s`, or `999` if never taken.

**Undrafted players are handled for free.** Boards are drawn for the whole pool, the
draft consumes `total_picks` of them, and the rest are simply never taken. No mixture
model, no special-casing.

---

## 7. Layer 5 — Calibration

`draft_model/calibrate.py`.

### The problem

Feed a player's ADP straight into the sampler and the simulation does **not** produce
that ADP back. He goes to whichever of ten managers rates him highest, so his draft
position is driven by an extreme of ten opinions rather than their centre.

Calibration is a **fixed-point loop**: simulate, measure how far the result sits from
the target, nudge the inputs, repeat. What comes out are `mu` and `sd` — the values the
sampler should be fed so the *draft it produces* matches the ADP measured in the real
world.

### `calibrate_sampler(adp_target, stdev_target, ...)`

Three details that each look optional and aren't:

**1. Common random numbers.** Every pass re-simulates with the same seed, so only
`mu`/`sd` differ. Without this, the change between two passes is mostly Monte Carlo
noise and the loop chases its own variance instead of converging.

**2. Damping (`ALPHA = 0.7`).** Moving the full distance to the target each pass
overshoots and oscillates, because the target moves when you move.

**3. Gating both updates on measurability.** This one prevented an actual divergence:

> For a player drafted in only half the simulations, the measured mean pick is
> conditional on **the drafts where he went early** — the runs where he'd have gone
> later record nothing at all. So it reads low, the residual reads high, `mu` gets
> pushed later, and he's drafted even *less* often. Positive feedback. Observed before
> the gate: one player's `mu` climbed **36.9 → 42.4 → 47.3** across three passes while
> his true target stayed at 31.

Players below the reliability threshold keep their input values. That's the honest
answer — their target is unreachable in a draft this size, and the vendor's own
measurement beats a statistic the simulation cannot compute.

### How much does calibration buy you?

Its value scales with how *wide* the spreads are, since that's what drives the
min-of-N effect:

```
sd = 1 + 0.05·adp    uncalibrated  0.37  →  calibrated  0.34
sd = 1 + 0.30·adp    uncalibrated  7.21  →  calibrated  4.02
sd = 1 + 0.60·adp    uncalibrated 30.77  →  calibrated 17.38
```

Real data sits in the regime where it matters. Live measurement: **4.53 → 0.61 picks**.

### `validate_sim(picks, ...)` — five checks before anything is trusted

| Check | What it catches |
|---|---|
| **Calibration** | Simulated ADP within tolerance of the target |
| **Pick count** | Exactly `total_picks` drafted in **every** simulation, not just on average |
| **Unique picks** | No pick number used twice within one draft |
| **Counting identity** | By pick *k*, exactly *k−1* players are gone |
| **Snake order** | Every team owns exactly one pick per round |

The counting identity is the valuable one and it costs nothing. It's **arithmetic, not
a modelling assumption**, and requires no data at all — which makes it catch sign flips,
off-by-ones and unit mixups that calibration would happily absorb.

Two error numbers are reported deliberately: one over reliably-drafted players (the
gate), one over everyone expected to be drafted (always worse). The second is shown so
a bad run can't hide behind a favourable subset. The gap is structural — 246 players
compete for 150 picks, so anyone with an ADP past the final pick can only ever be
drafted *earlier* than their ADP.

---

## 8. Layer 6 — Saving results

`draft_model/artifacts.py`. Simulation happens **offline**; the app only ever loads
results. Streamlit re-runs its whole script on every interaction, so anything expensive
must not live there.

**`save_picks_matrix(path, picks, config, player_ids, ...)`** writes a compressed
`.npz` (about 1.8 MB for 10,000 × 246).

> **The one rule.** A picks matrix on its own is meaningless. `picks[:, 47]` is a column
> of numbers with no indication of *which player* it describes — and the ordering came
> from a table sorted at a particular moment from a particular data pull. So the player
> ID list is stored **with** the matrix, and a file missing it is treated as corrupt
> rather than loaded hopefully.

**`SimArtifact.column_for(player_id)`** is the only correct way to index by player.
It raises for an unknown player rather than returning something plausible.

**`artifact_path(directory, draft_id, config)`** embeds `config.fingerprint()` in the
filename, so a settings change is a cache *miss* rather than a stale hit.

**What the fingerprint covers, and what it deliberately doesn't:**

| Included | Excluded |
|---|---|
| year, num_teams, num_rounds | `draft_position` — decides which picks you *look at* |
| scoring_format, **platform** | `starting_slots` — sets replacement level, recomputed on load |
| keepers, third_round_reversal, seed | `roster_size` — bookkeeping only |

Everything excluded is recomputed whenever a board loads, so a stale value can't
survive. Including them would be the safe-looking choice, but it means editing your
lineup forces a full re-simulation producing a byte-identical matrix.

> `platform` **is** included, and that was a bug fix: it re-weights the ADP blend, which
> moves `adp_target` (measured: mean 2.3 picks, up to 11.8), which changes the
> simulation. Before it was part of the config, switching platforms silently served the
> previous platform's matrix.

**`matches_table(artifact, table)`** catches the other kind of staleness — same
settings, but the *data* moved (a newer FFC pull re-sorted the pool). It checks
**order**, not just membership, since same players in a different order would make
every column refer to the wrong person.

---

## 9. Layer 7 — Asking questions

`draft_model/queries.py`. Everything here reads a completed simulation; nothing
simulates.

### 9.1 Availability

**`prob_available_at_pick(picks, player_idx, target_pick)`** — count and divide.
Undrafted counts as available automatically, since `999` exceeds any real pick number.

**`availability_matrix(picks, target_picks)`** — the whole grid at once. In a snake
draft you only ever need availability at *your own* picks — about fifteen numbers, not
two hundred.

### 9.2 Tier survival — and a trap

**`prob_any_available(picks, player_idxs, target_pick)`** — "will at least one of these
four backs get back to me?"

> **Use `max`, not `min`.** This is worth stating loudly because the reverse is a
> natural-looking mistake:
>
> | Expression | Actually means |
> |---|---|
> | `picks[:, group].max(axis=1) >= k` | **at least one** is still available |
> | `picks[:, group].min(axis=1) >= k` | **every** one is still available |
>
> `min >= k` says the *earliest-drafted* member went at or after k, which can only be
> true if none went before. For any tier containing an early-round player it returns 0%
> at every pick you care about, while looking entirely reasonable.

**How much does the joint calculation buy you?** Measured across 392 real tier queries
against the independence approximation: mean difference 0.6 pp, 95th percentile 2.0 pp,
**maximum 9.2 pp**. So marginals are usually a decent approximation, but the tail
matters — 88.6% vs 79.3% on a five-player tier is a materially different decision. The
joint value is always the *higher* one, because the players compete for the same picks.

### 9.3 Value over replacement

**`replacement_value(projections, positions, starting_slots, num_teams)`** — the
projected points of the last *startable* player at each position.

Why: raw projections say a quarterback is worth more than a running back simply because
quarterbacks score more points. What matters is the gap to the player you could have
*instead*, which depends on how many of each position get started league-wide.

**FLEX is derived, not split by a constant.** Rather than assuming a flex is 45% RB /
45% WR / 10% TE, take the top `num_teams × (RB + WR + TE + FLEX)` flex-eligible players
by projection as the startable set, and let each position's replacement be the worst
startable player *at that position*. The allocation falls out of the projections and
adapts to any league shape.

**`compute_vorp(projections, positions, replacement)`** — `projection − replacement`.
NaN propagates deliberately: K and DST have no projections, and inventing a zero would
make them look exactly replacement-level rather than *unknown*.

### 9.4 Cost of waiting — the numbers the page actually shows

**`cost_of_waiting(picks, player_idx, my_next_pick, vorp, positions)`**:

```
P(gone by my next pick) × (his VORP − what I'd actually end up with instead)
```

> **The fallback comes from the simulation, not the current board.** An earlier version
> defined it as "the best other available player at his position", which is degenerate
> pre-draft — every player is nominally available, so Bijan Robinson's fallback was
> Jahmyr Gibbs, who is equally certain to be gone. **It returned 0.0 for a 155-VORP back
> with a 4% chance of lasting**, and only the single best player at each position ever
> scored above zero.

**`positional_cost_of_waiting(picks, position, at_pick, my_next_pick, ...)`**:

```
E[best available at this pick] − E[best available at my next pick]
```

**This is the number that chooses between positions**, and it is not a sum of the
per-player costs:

- It prices the **whole tier**. Eight interchangeable running backs score near zero even
  though each individually is probably gone — losing any one barely matters. The
  per-player metric would show eight separate alarming numbers.
- Conversely one elite back with a cliff behind him scores high even at modest odds.

Both sides come from the simulation. Using the *global* best VORP as "best available
now" was another bug: evaluating round 3, it reported the cost of losing Christian
McCaffrey, who is 0% available by then. You can't wait on a player you could never have
had.

**`expected_best_at_pick(...)`** is the shared core of both. It's an expectation over a
**maximum over survivors** — exactly the kind of quantity marginal probabilities cannot
reconstruct, and the strongest reason to keep the full matrix rather than a summary.

---

## 10. Layer 8 — Reaching the app

### `services/draft_sim_service.py`

**`DraftSimService`** is the seam where the pure numerical core meets the app's data.

- **`build_model_table(config)`** — pulls FFC, platform ADP and projections, assembles
  the table. **Shared with `scripts/run_draft_sim.py`**, so the script and the app can't
  build subtly different tables.
- **`load_board(draft_doc, year)`** — returns a `DraftBoard`, or raises with the exact
  command to fix a missing simulation.

**`DraftBoard`** bundles the table, artifact, VORP and replacement level **already
aligned**: `table` row *i* describes `artifact.picks[:, i]`, and `vorp[i]` is that same
player. Keeping them together means the alignment invariant doesn't have to be
re-established at every call site.

Its methods map directly onto what the page shows:

| Method | Produces |
|---|---|
| `availability(target_picks)` | One row per player: availability at each pick, VORP, cost of waiting |
| `positional_costs(at_pick, next_pick)` | Per-position cost of waiting, most urgent first |
| `tier_survival(player_names, target_pick)` | "84% chance at least one of these is available" |

### `pages/draft_plan.py`

The Streamlit page. The **Round** dropdown drives everything — picking `3.04` sets the
current pick to 24 and the next to 37.

- A metric row of per-position cost of waiting.
- **Avail** columns (percentage bars) on both the options list and the ranked board.
- **Cost** on the board — points lost if you pass and he's gone.
- A tier-survival caption above each board, since your shortlist for a round *is* a tier.

Two UI decisions worth knowing:

- The artifact loads behind `@st.cache_resource`, so page re-runs don't re-read it.
- Players with no simulation row show **blank, not 0%**. FFC lists 246 players while the
  app's universe has 307, so the deepest ~60 have no row. `0%` would read as "certain to
  be gone" — the opposite of "not in the simulated pool".

Reading it in practice:

```
 round  pick -> next     QB    RB    WR    TE
  1.04     4 ->   17      5   110    96     0
  2.07    17 ->   24     12    13     3     4
  3.04    24 ->   37     24    17     6    43      <- the elite TE cliff
  4.07    37 ->   44      4     7     3    14
```

Round 1 says take a skill player. Round 2 is flat — nothing is urgent. Round 3 spikes
TE, which is the elite tight ends falling off a cliff.

---

## 11. How to run everything

```bash
# 1. Validate hand-placed CSV files BEFORE loading them
python scripts/check_data_files.py

# 2. Load every CSV into MongoDB, and pull the current season from FFC
python scripts/load_data.py
python scripts/load_data.py --skip-ffc        # CSVs only, no network

# 3. One-time: archive historical FFC seasons (insurance only)
python scripts/ingest_ffc_history.py --dry-run
python scripts/ingest_ffc_history.py

# 4. Build the table, calibrate, simulate, validate, save
python scripts/run_draft_sim.py --list                # which drafts exist, and
                                                      #   whether each has a CURRENT run
python scripts/run_draft_sim.py --dry-run             # everything except the write
python scripts/run_draft_sim.py                       # the first/only draft
python scripts/run_draft_sim.py --draft-id abc123     # one specific draft
python scripts/run_draft_sim.py --all                 # every saved draft
python scripts/run_draft_sim.py --all --skip-existing # only what's missing
python scripts/run_draft_sim.py --no-calibrate        # measure what calibration buys

# 5. Run the app
streamlit run streamlit_app.py

# 6. Tests (105, all offline except the FFC adapter's fixtures)
pytest
```

### When do you need to re-simulate?

**Simulations are never re-run automatically.** They are produced by the script and read
by the app; the app never simulates. Whenever a setting in the fingerprint changes, the
saved run stops matching and the page shows a warning naming the command to fix it.

| Changing this… | Needs a re-run? | Why |
|---|---|---|
| Teams, rounds, scoring format | **Yes** | Changes how the draft unfolds |
| **Platform** | **Yes** | Re-weights the ADP blend, moving `adp_target` by ~2.3 picks on average |
| Keepers, random seed | **Yes** | Different pool, different randomness |
| Draft position | No | Only decides which picks you *look at* |
| Starting lineup slots | No | Only sets replacement level, recomputed on load |
| Roster size | No | Bookkeeping only |

`--list` reports which drafts are current and which are missing or out of date, so the
usual maintenance command is:

```bash
python scripts/run_draft_sim.py --all --skip-existing
```

A full run costs about 14 seconds, of which roughly 10 is assembling the data (FFC,
three platforms' ADP, three analysts' projections, identity resolution) and only about
4 is calibration plus the 10,000 simulated drafts.

---

## 12. Invariants: the rules you must not break

Violating any of these produces **silently wrong numbers that look plausible**.

**1. Table row order IS picks-matrix column order.**
`table.iloc[i]` describes `picks[:, i]`, always. Never sort, filter or reindex the table
after a simulation without regenerating the matrix.

**2. Picks are 1-indexed; team IDs are 0-indexed.**
Pick 1 is the first overall selection; team 0 is the first slot.

**3. Undrafted is `999`, and every statistic must mask it.**
Forgetting to would drag averages toward 999 and produce garbage that's still numeric.

**4. `mu`/`sd` are calibrated parameters; `adp_target`/`stdev_target` are targets.**
Only the first pair ever reaches the sampler. Mixing them up is the easiest way to get
plausible-looking wrong output.

**5. The sim table is keyed by `ffc_player_id`. `canonical_id` is nullable.**
Used only by the display layer, **never as a filter**. Defenses can't resolve, and
dropping them would distort every pick after them.

---

## 13. What is deliberately not built

Recorded as decisions, not oversights.

**No stdev prediction model.** FFC's raw ADP and spread are archived (2020–2024 plus
the current season) purely as the *option* to build one if FFC ever disappears. No code
reads that archive, and nothing in the pipeline depends on a fitted width model — the
two places that once did now use the neighbour-median fallback and a "leave it alone"
rule instead. `draft_model/STDEV_MODEL_LONGTERM.md` retains the full method as a
reference for a day that may never come.

**No platform ADP archiving.** ESPN/Yahoo/Sleeper aren't snapshotted. The cost is
recorded: any future model would be trained on FFC ADP and served platform ADP, and
trailing ADP volatility is *unavailable* rather than merely difficult.

**No in-draft tool yet.** Nothing tracks a live draft. The engine already has the hooks
(`start_pick`, `end_pick`, `already_drafted`, `roster_counts`), so `sim_from_state`
would be a thin wrapper — but the live draft log and its UI don't exist. At 0.19 ms per
draft, running ~11 picks forward between your turns is comfortably fast enough.

**K/DST are loaded but outside the player universe.** Their UDK files turned out to be
*rankings*, not projections — no points, so no VORP is possible for them regardless.
The simulator handles K/DST from FFC and doesn't need these.

**Three judgment constants that are not fitted values** (`draft_model/config.py`):

| Constant | Value | Status |
|---|---|---|
| `RHO` | 0.35 | Unfittable — confounded with `sd`. Revisit with real draft logs. |
| `PLATFORM_WEIGHT` | 0.5 | Worth a sensitivity sweep; if 0→1 barely moves anything, cut the mechanism. |
| `STARTER_DEADLINE` / `NEED_BONUS` | see config | Control how strongly positional runs emerge. Tune against real drafts. |

---

## Where to go next

| To understand… | Read |
|---|---|
| The reasoning behind every design decision | `draft_model/DESIGN.md` |
| What's built and what's left | `draft_model/TODO.md` |
| The simulator's hot loop | `draft_model/engine.py`, `sim_batch` |
| Why the numbers can be trusted | `draft_model/calibrate.py`, `validate_sim` |
| What the page actually shows | `services/draft_sim_service.py`, `DraftBoard` |

The tests are also documentation. Several encode bugs that were found and fixed —
`test_calibration_does_not_run_away`, `test_prob_any_available_uses_max_not_min`,
`test_cost_of_waiting_uses_the_simulated_fallback_not_the_current_board` — and each
explains in its comments what went wrong and why the fix is shaped the way it is.

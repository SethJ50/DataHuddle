# Draft Model — System Design

**Status:** draft for critique. This is the single reference for the draft model. Companion
docs in this directory: `TODO.md` (build checklist), `SPREAD_SOURCES.md` (vendor API
inventory), `STDEV_MODEL_LONGTERM.md` (a method reference for a stdev model that is **not**
planned work — see § 5.5).

---

## 1. What this is and why

DataHuddle can currently tell you *where the market ranks a player* (ADP across
ESPN/Yahoo/Sleeper) and *what you think he's worth* (FFB/UDK projections). It cannot answer
the question that actually drives draft-day decisions:

> **Will he still be there at my next pick, and does it matter if he isn't?**

This builds the underlying draft model — a Monte Carlo simulator that plays out thousands of
fake drafts — so the app can produce availability probabilities, tier-survival probabilities,
and cost-of-waiting numbers. Everything on the Draft Plan page eventually hangs off it.

**The two axes, kept separate on purpose:**

- **ADP is the timing axis.** It says *when* a player goes.
- **Projections are the value axis.** They say *what he's worth.*

Availability needs the first. Cost-of-waiting needs both. Conflating them is the single most
common way this kind of model goes quietly wrong.

---

## 2. Decisions locked in

| Question | Decision |
|---|---|
| Sim pool | FFC's full pool **including K/DST**. UDK stays the *display/analytics* universe. UDK K/DST data to be added if obtainable. |
| Center / width | FFC ADP + FFC stdev as the coherent base; platform ADP applied as an explicit, weighted **shift**. |
| Execution | Offline script writes an artifact; app loads it. In-draft conditional sims run live in-app later. |
| Build order | FFC ingest → sim → remaining data layer (`blend_adp`, projections trio). |
| League config additions | Starting lineup slots, keepers, bench/total roster size. Third-round reversal supported in code, not surfaced as a setting. |
| Manager model | Shared-consensus shock + idiosyncratic noise (`rho`), which strictly generalizes the independent model. |
| In-draft tool | Deferred to Phase 5. Hooks designed in from the start. |
| Width durability | No stdev model is built or planned. FFC's raw ADP/stdev is archived (2020–2024 + ongoing) purely as the option to build one later; nothing in the pipeline depends on it (§ 5.5). |
| Storage | Current-state collections stay wipe-and-replace on `load_data`. One append-only `adp_snapshots` collection, dated, never overwritten. **FFC only** — platform ADP archiving deferred (§ 5.5). |

---

## 3. Two non-obvious design choices

Both are places where the natural first implementation is wrong in a way that doesn't
announce itself. Written out at length because they are easy to "simplify" back into the
broken version later.

### 3.1 Vectorize across *simulations*, not across picks

The obvious implementation simulates one draft as a Python loop over ~180 picks, then runs
that loop 10,000 times. When that turns out to be far too slow, the obvious fix is to make
each individual draft cheaper — pre-sort each board and walk a pointer past taken players, or
evaluate only the next ~15 available entries instead of rescanning the whole pool.

**Don't. That optimizes the wrong axis, and the pointer-walk fix trades correctness for speed
we don't need.**

The key observation: snake order is deterministic. At pick 47, **team 3 is on the clock in
every single simulation.** So every simulation can advance one pick together, as one numpy
operation.

```python
# B = batch of sims, P = players, T = teams, n_pos = number of positions
boards = ...                              # (B, P, T) float32, drawn once per sim
taken  = np.zeros((B, P), bool)
counts = np.zeros((B, T, n_pos), np.int8) # roster counts per sim, per team, per position
picks  = np.full((B, P), UNDRAFTED, np.int16)

for pick in range(start_pick, end_pick + 1):
    t = snake_order(pick, T, third_round_reversal)   # identical across the whole batch

    v = boards[:, :, t].copy()                       # (B, P) this team's board this sim
    have = counts[:, t, :][:, pos_index]             # (B, P) count at each player's position
    v += BLOCK * (have >= hard_limit[pos_index])     # roster-full -> effectively unpickable
    v -= NEED  * ((have == 0) & (pick > deadline[pos_index]))   # reach for empty starter slot
    v[taken] = np.inf

    choice = v.argmin(axis=1)                        # (B,) one selection per sim
    rows = np.arange(B)
    picks[rows, choice] = pick
    taken[rows, choice] = True
    counts[rows, t, pos_index[choice]] += 1
```

- **Cost:** `n_picks × n_sims × P` ≈ 180 × 10,000 × 237 ≈ 430M element-ops.
  **Measured 2026-07-31 on the real 237-player pool: 0.2 ms per draft, ~2 seconds for
  10,000 drafts.** Comfortably better than the tens-of-seconds this section originally
  estimated. The nested Python loop is minutes-to-hours.
- **Memory:** a batch of 250 sims is 250 × 350 × 12 × 4 bytes ≈ 4 MB. Batching is what keeps
  `boards` from being 144 GB at the full 10,000.
- **Consequence:** the pointer-walk and 15-player-window compromises are **unnecessary and
  should not be built.** They exist only to rescue the per-draft loop.
- `effective_value()` survives as a **scalar reference implementation** that tests assert the
  vectorized path against. It is not on the hot path.

Two details that are easy to get wrong inside the batched loop:

- **Boards are drawn once per simulated draft, never per pick.** Re-drawing mid-draft makes
  each pick forget what the last one believed, so a player can be nearly taken at pick 10 and
  inexplicably survive to 60.
- **Need adjustments are applied at read time, never written back into the board.** Written
  back, they compound over sixteen rounds into nonsense.

### 3.2 `rho` is not identifiable from ADP + stdev — set it, don't fit it

Each manager's private board value is drawn as:

```python
z_shared = rng.standard_normal((B, P, 1))     # one draft-wide opinion shock per player
z_idio   = rng.standard_normal((B, P, T))     # each manager's private deviation
boards = mu[None, :, None] + sd[None, :, None] * (
    rho * z_shared + np.sqrt(1 - rho**2) * z_idio
)
```

The nice property: a manager's marginal value for a player is exactly `N(mu, sd)` for **any**
`rho`. `rho` only controls how much the twelve managers agree *within a given draft*.
`rho = 0` reproduces the fully independent model exactly, so this is a strict generalization
at the cost of one extra line.

**The catch, stated plainly: `rho` and `sd` are confounded.** A higher `rho` softens the
min-of-twelve drift, so the calibration loop simply refits `mu`/`sd` around whatever `rho`
you chose and hits the same ADP and stdev targets either way. Marginal targets cannot
identify a correlation parameter — there is no amount of ADP and stdev data that pins it down.

**So: hold `rho` fixed during calibration and treat it as a documented judgment parameter
(default `0.35`).** Building a loop that appears to fit it would produce a confident-looking
number that means nothing.

Where `rho` actually matters is *joint* queries — `prob_any_available` across a tier — which
is precisely where independence is least defensible and where we currently have no data to
fit against. Revisit it once real draft logs are being recorded (§ 10, cross-cutting).

---

## 4. Architecture

Follows the existing `adapters → repositories → services → pages` convention. The numerical
core is deliberately free of Streamlit and Mongo so it can be unit-tested standalone.

```
draft_model/                      # pure numpy/pandas. no streamlit, no mongo.
    __init__.py
    config.py                     # DraftConfig, HARD_LIMIT, STARTER_DEADLINE, RHO
    table.py                      # build_table(), blend_adp(), platform shift
    mechanics.py                  # snake_order(), effective_value() [reference impl]
    engine.py                     # draw_boards(), sim_batch(), monte_carlo_sim()
    calibrate.py                  # calibrate_sampler(), validate_sim()
    queries.py                    # prob_available_at_pick(), prob_any_available(),
                                  #   replacement_value(), compute_vorp(),
                                  #   cost_of_waiting(), positional_cost_of_waiting()
    artifacts.py                  # save/load .npz picks matrix + metadata

adapters/ffc_adapter.py           # FFC JSON -> canonical shape (vendor quirks live here)
repositories/ffc_repo.py          # reads the ffc_adp current-state collection
repositories/adp_snapshot_repo.py # reads/appends adp_snapshots (all sources, dated)
services/draft_sim_service.py     # wiring: config -> table -> artifact -> query results
scripts/ingest_ffc_history.py     # ONE-TIME backfill of past years
scripts/snapshot_adp.py           # dated append of every ADP source; run from load_data
scripts/run_draft_sim.py          # offline 10k run -> data/sim/{draft_id}.npz
tests/test_draft_model.py         # snake_order, counting identity, vectorized-vs-scalar
```

`AppContext.__init__` gains `self.draft_sim_service = DraftSimService(...)` alongside the
existing services.

### What already exists that this plugs into

Worth being explicit, because the sim needs far less new plumbing than it looks like:

| Existing | Role for the draft model |
|---|---|
| `RosterService.roster()` | The display universe (UDK-ranked QB/RB/WR/TE). Already carries `adp`, `upside`, `risk`, `tier`, `points`. |
| `ProjectionsService.get_own_projections()` | The value axis. Feeds replacement level and VORP. |
| `PlayerIdentityRepo.resolve_many_with_fallback()` | How every vendor joins to `canonical_id`. FFC uses `source="ffc"`. |
| `DraftService` | Stores per-draft league settings. Gains three fields (§ 6). |
| `DraftPlanService.pick_labels()` | Already computes which overall picks are yours in a snake draft. |
| `CollectionRepo` | Cache-forever wrapper every Mongo source reads through. |

### Invariants

Violating any of these produces silently wrong numbers that look plausible.

1. **Player table row order IS picks-matrix column order.** `table.iloc[i]` describes
   `picks[:, i]`, always. Never sort, filter, or reindex the table after a sim without
   regenerating the matrix.
2. **Picks are 1-indexed; team IDs are 0-indexed.**
3. **Undrafted is the sentinel `999`**, not 0 and not NaN. Every statistic over pick numbers
   must mask it out.
4. **`mu`/`sd` are calibrated sampler parameters and the only values ever passed to the
   sampler. `adp_target`/`stdev_target` are validation references only.** Mixing these up is
   the easiest possible way to get plausible-looking wrong output.
5. **The sim table is keyed by `ffc_player_id`. `canonical_id` is a nullable join column.**

### Why invariant 5

The sim needs only `adp`, `stdev`, and `position` — it does not need identity resolution. K
and DST will never resolve to an nflreadpy `gsis_id` (a defense isn't a player), and forcing
them through `PlayerIdentityRepo` would either drop them or require inventing fake IDs.

So `canonical_id` rides along as a nullable column used **only by the display/analytics
layer**. Unresolved FFC players still occupy picks in the simulation — which is the entire
point of including K/DST — they just aren't individually queryable from the UI. This
decouples the sim from a join that can only ever fail for exactly two positions.

---

## 5. Data layer

### 5.1 Storage — current state vs. the snapshot layer

Two kinds of collection, never merged. Keeping them separate is what lets you answer, six
weeks from now, whether a number changed because the *data* changed or because your *code*
did.

**Current state** — what the app reads. Wipe-and-replace on every `load_data` run, exactly
the existing paradigm: `ffc_adp`, `espn_projections`, `sleeper_projections`,
`yahoo_draftanalysis`.

**`adp_snapshots`** — append-only, dated, every source in one collection. Never overwritten.
This is raw material kept as insurance (§ 5.5), not an input to anything. No code reads it.

```
source          "ffc" | "espn" | "sleeper" | "yahoo"
snapshot_date   "2026-08-14"          # date, not timestamp — the dedupe key
pulled_at       full ISO timestamp    # provenance
season          2026
format          "half-ppr"            # null where the source has no format split (Yahoo)
num_teams       12                    # null where not applicable
player_key      the source's own id, or normalized name where it has none
name, pos, team
adp
stdev, high, low, times_drafted       # nullable — FFC only, today
content_hash    hash of this source's payload for this snapshot
```

**Why one collection rather than one per source.** This collapses the separately-planned
`ffc_adp_history` into the general snapshot layer — a change worth calling out. The reason is
that every analytical query wants the sources together:

- **Cross-source ADP disagreement** (a Tier 2 predictor) becomes one `groupby`, not a
  four-way join.
- **Trailing ADP volatility** (Tier 3, and `STDEV_MODEL_LONGTERM.md` calls it "the best Tier 3
  feature by a distance") becomes one `groupby`.
- Mongo is schemaless, so the nullable spread columns cost nothing for the three sources that
  don't have them.

`scripts/ingest_ffc_history.py` writes historical years into this same collection with
back-dated `snapshot_date` values.

**Idempotent on `(source, season, format, num_teams, player_key, snapshot_date)`.** Running
the job five times in one day writes one snapshot, so it's safe to hang off `load_data`
without worrying about how often that runs during development.

#### The stale-CSV trap

Platform ADP does not arrive from an API — it comes from manual scrapes into CSVs
(`scripts/scrape_espn_projections.py`, `scrape_sleeper_projections.py`, and Yahoo's browser
console script). **If the CSV isn't re-scraped, snapshotting it weekly appends N identical
rows, and computed ADP volatility reads as exactly zero.** That's worse than missing data,
because zero volatility looks like a real, confident measurement.

Hence `content_hash`: skip the append when a source's payload is byte-identical to its
previous snapshot. Volatility is then computed over genuinely distinct observations, and a
source that has stopped updating shows up as a **gap** rather than as false stability.

**Honest limitation:** ESPN and Sleeper have Python scrapers and can run headless before the
snapshot. Yahoo's is a browser console script and cannot be automated as-is, so Yahoo will
have a sparser, more irregular series. The feature pipeline must tolerate uneven sampling per
source rather than assuming a clean weekly grid for all four.

**Volume:** roughly 600 players × 4 sources × weekly × 20 weeks ≈ 48k documents per season.
Trivial for Mongo, and worth keeping indefinitely.

### 5.2 FFC adapter details

**Everything below was verified against the live endpoint on 2026-07-30, not taken from
documentation.** Three of these findings contradict what the API's shape suggests.

Endpoint: `https://fantasyfootballcalculator.com/api/v1/adp/{format}?teams={n}&year={y}`

Response is `{status, meta, players}`:

```
meta      {type, teams, rounds, total_drafts, start_date, end_date}
players[] {player_id, name, position, team, adp, adp_formatted,
           times_drafted, high, low, stdev, bye}
```

#### Verified behaviour

- **`teams` is echoed but ignored.** ADP is byte-identical across `teams=8/10/12/14`, and
  `total_drafts` is the same for all of them. **FFC's ADP is not league-size-specific.**
  Still pass a valid value — 13 and 16 return HTTP 400 — but snapping `num_teams` is
  cosmetic, and `num_teams` on a snapshot row is provenance, not a data selector. This
  weakens FFC as a match for your specific league and correspondingly *strengthens* the case
  for the platform shift (§ 5.3).
- **`format` genuinely matters**: 2026 returns 246 players for `ppr`, 204 for `half-ppr`,
  186 for `standard`, 211 for `2qb`. `dynasty` and `rookie` return **zero** players — not
  usable. Mapping: `REGULAR → "standard"`, `HALF_PPR → "half-ppr"`, `FULL_PPR → "ppr"`.
- **Positions are `PK` and `DEF`**, not `K` and `DST`. `registry.parse_positions()` already
  maps `DEF → DST`; **`PK → K` must be added.** 2026 ppr: WR 84, RB 63, QB 29, DEF 27,
  TE 24, PK 19.
- **`rounds` is always 15**, so FFC ADP describes a 180-pick draft at 12 teams. With only 246
  players returned, the `total_picks × 1.5` pool cap (270) never binds — keep it as a guard,
  but expect it to be a no-op.
- `year` is required. Forgetting it silently returns a different season.
- FFC filters out autodraft picks (human selections only). This is *why* its stdev is usable:
  autodrafted picks follow the platform's default list exactly and would artificially collapse
  the spread.
- Updates once daily. Never call per request — pull on `load_data`, cache in Mongo.
- **Known bug to fix on promotion:** `draft_model/fantasy_football_calculator.py` hardcodes
  `params={"teams": 12, "year": 2026}` while accepting `teams` and `year` as arguments.

#### Two distinct "no data" signals — this breaks the obvious ingest loop

| Condition | Response |
|---|---|
| Year outside accepted range (e.g. 1998) | HTTP **400**, `{status: Error, errors: ["Invalid year"]}` |
| Accepted year, no data (2025, 2009, 2008) | HTTP **200**, `players: []` |

**`ingest_ffc_history.py` must NOT walk backward until it fails.** Verified coverage for
`ppr`/12 is **2010–2024 and 2026 — 2025 is empty across every format**, a hole in the middle
of the series. A walk-until-failure loop stops at 2025 and silently misses fifteen years of
training data.

Iterate a fixed candidate range instead (say 2005–current), treat both signals as "skip", and
report the coverage actually found. `--dry-run` exists precisely so this is visible before
anything is written.

#### What `meta` buys you, and a caveat about history

Store the whole `meta` block on every snapshot row. `total_drafts` is a sample-size signal,
and `start_date`/`end_date` give the true observation window.

That window matters more than it looks. Historical years are **2-to-4-day snapshots taken at
season start** (2024: Aug 31 → Sep 1). The current year is a rolling recent window
(2026: Jul 23 → Jul 30). So the backfill yields roughly **one annual observation per year, not
a within-season time series.**

**Consequence for Phase 7:** trailing-4-week ADP volatility — the strongest durable predictor
in `STDEV_MODEL_LONGTERM.md` — **cannot be reconstructed from the backfill at all.** The
backfill gives cross-sectional depth (many players, one date per season); within-season
dynamics can only come from repeated snapshots taken as the season unfolds.

With platform archiving deferred (§ 5.5) and FFC snapshots taken only when `load_data` runs,
**treat ADP volatility as unavailable for the first version of the Phase 7 model.** It is not
a feature to be engineered around — it's a feature that does not exist yet. Building the
model without it, and adding it later once a series exists, is the honest sequence.

#### When `stdev` is unusable

FFC publishes the width directly, which skips the hardest part of the whole project. A small
number of players still come back without a usable one, and the pipeline must fill those
**without depending on any trained model** (§ 5.5).

Measured on the 2026 pull, so the fallback can be sized honestly rather than defensively:

| Format | Players | Unusable `stdev` |
|---|---|---|
| PPR | 246 | **1** |
| Half-PPR | 204 | **0** |
| Standard | 186 | **0** |

The single case is Zachariah Branch: `times_drafted=5`, `high = low = 177`, so `stdev = 0.0`
*and* the `(high - low) / 4` fallback is also 0. Zero is not a narrow width — it means FFC had
nothing to measure. Left as 0 he would be perfectly deterministic in the sampler.

**The fallback chain, in order:**

1. **`stdev` from FFC**, whenever it's present and non-zero. This is the overwhelming majority.
2. **`(high - low) / 4`**, when `stdev` is absent but the range is real. For a roughly
   bell-shaped spread the range covers about four standard deviations. Crude, and it
   overreacts to one drafter who reached, but calibration (§ 7) absorbs much of the error.
3. **Median `stdev` of the 20 nearest-ADP players at the same position.** No training, no
   saved artifact, no historical data — computed fresh from the same table each time.

Why step 3 is defensible rather than a guess: spread rises smoothly and predictably with ADP,
so neighbours genuinely carry information. Median `stdev` by ADP band, 2026 PPR:

```
adp   (0,24]  (24,60]  (60,100]  (100,150]  (150,200]
      2.65     6.00     10.35      15.15      15.95
```

**Restricted to the same position** because QB and TE spread differently from RB and WR.
Verified there is always enough to work with — the thinnest position (K) still has 19 usable
players, and every other position has 24+. For Branch this yields **15.00**, against 15.15
from an any-position neighbourhood.

**Thin samples are deliberately left alone.** 68 of 246 PPR players (and 138 of 186 in
Standard) have `times_drafted < 50`, so their `stdev` is noisy but *present*. An earlier
version of this design blended those toward a smoothed width by sample size. That is dropped:
calibration already adjusts `sd` to reproduce the target spread, and thin-sample players are
almost entirely deep players who barely influence availability at your actual picks. Using
FFC's noisy value directly costs little and removes a concept from the pipeline.

`times_drafted` is still carried on the table — as a diagnostic, and so this decision can be
revisited with evidence rather than reflex.

### 5.3 The platform shift

`build_table()` produces the target the sim must reproduce:

```python
adp_target = ffc_adp + platform_weight * (platform_blend_adp - ffc_adp)
```

- `platform_blend_adp` comes from `blend_adp(sources, weights)` over ESPN/Yahoo/Sleeper,
  weighted toward the platform recorded in `draft["platform"]`. The default player list a
  platform shows in-app anchors your actual leaguemates far more strongly than any consensus
  ranking does.
- `blend_adp` renormalizes weights **per player** across whichever sources actually have him.
  A deep player present only in Sleeper should get Sleeper's ADP, not Sleeper's ADP scaled
  down by Sleeper's fractional weight.
- The shift applies only to players present in **both** FFC and the platform blend. Everyone
  else keeps pure FFC ADP.
- `platform_weight = 0.5` by default. `0.0` recovers pure FFC — keep that path working, it's
  the clean fallback whenever the platform CSVs go stale.
- **`stdev_target` stays raw FFC.** The shift moves the center; there is no defensible reason
  for it to touch the width.

**The population mismatch this resolves:** FFC's stdev measures spread within *FFC's own*
drafts around *FFC's own* ADP. Pairing that width directly with blended platform ADP would
silently mix two different populations. Instead the sim centers on one coherent population,
and the platform anchor becomes an explicit, tunable, reviewable adjustment.

**`adp_target` and `stdev_target` are what `calibrate_sampler` reproduces and what
`validate_sim` checks against** — not raw FFC ADP.

### 5.4 The resulting table

One row per FFC player. Row order defines picks-matrix column order (invariant 1).

```
ffc_player_id | canonical_id (nullable) | name | position | adp_target | stdev_target
              | times_drafted | projection (nullable) | upside | risk | mu | sd
```

`mu` and `sd` are initialized to `adp_target` / `stdev_target` and are **meaningless until
`calibrate_sampler` has run and written back to them.**

Column is `position`, not `pos` — matching every existing adapter in the repo
(`adp_source_adapter`, `ffb_projections_adapter`, `udk_rankings_adapter`) and what
`FfcAdapter` actually emits.

**Pool cap:** drop players with `adp_target > total_picks * 1.5`. A player with ADP 400 in a
180-pick draft contributes nothing and costs exactly as much to simulate as anyone else.

**Keepers are not removed here.** The table is the full universe; removal happens per-sim, so
one table serves both keeper and redraft scenarios.

---

### 5.5 Width durability — logging only, no model

**Decision (2026-07-30): no stdev model is being built, and nothing in the pipeline depends
on one.** This section used to specify a surrogate model trained to predict `stdev` from
durable predictors. That is no longer planned work.

**The problem it addressed is real and unchanged.** ADP is safe — every source publishes it.
Width is fragile: FFC is the only source this app has, and it's a free API with no SLA. The
simulator cannot run without a width per player.

**What we're doing instead: keeping the raw material, and nothing more.**

- The 2020–2024 backfill and ongoing current-season snapshots write FFC's `adp`, `stdev`,
  `high`, `low`, and `times_drafted` into `adp_snapshots` (§ 5.1).
- That archive is the *option* to build a model later. It is not a model, and no code reads
  it.
- If FFC ever disappears, that stored history is what makes a replacement estimator possible
  at all. Collected after the fact, it would be worth nothing — which is the entire reason
  the logging happens now while the modeling doesn't.

**The pipeline must not acquire a dependency on this.** Two places previously did, and both
have been removed:

| Where | Was | Now |
|---|---|---|
| § 5.2 unusable `stdev` | Blend toward a modeled sigma | Median of 20 nearest-ADP players at the same position |
| § 7.2 calibration | Fit `log(sd) ~ log(adp)` for unreliable players | Leave `sd` at its input value; don't update what can't be measured |

Neither replacement trains anything, saves an artifact, or reads history. Both are computed
from the table in front of them.

**Known limitations, accepted deliberately** — recorded so they aren't rediscovered as
surprises rather than choices:

- **Only 2020–2024 is archived.** FFC still serves 2010–2019; not pulled, on the view that
  pre-2020 fantasy is a different game. Five seasons supports roughly one honest
  train-on-past/test-on-future split, and the older years may not be retrievable later.
- **Platform ADP is not archived at all.** Any future model would therefore be trained on FFC
  ADP and served platform ADP — train/serve skew of unknown size, since there's no platform
  history to measure it against.
- **ADP volatility is unavailable, not merely hard.** `STDEV_MODEL_LONGTERM.md` calls it the
  strongest durable predictor. Historical FFC rows are single 2–4 day windows per season
  (§ 5.2), so there is nothing to compute a trailing change from. Only the ongoing
  current-season snapshots will ever build such a series, and only slowly.

**If the problem ever actually arrives**, `STDEV_MODEL_LONGTERM.md` retains the full method —
austere vs rich models, `log(stdev)` as the target, season-based validation, the four saved
artifacts. It is a reference for a day that may never come, not a plan.

---

## 6. League configuration

`DraftService` gains three fields, with matching controls on `pages/draft_manager.py`:

- **`starting_slots`** — e.g. `{"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1,
  "DST": 1}`. This is what makes replacement level correct at any league size. Hardcoding
  replacement ranks (the 12-team 1-QB baseline is QB12 / RB30 / WR36 / TE12 — the last
  startable player at each position) works only for that exact league and is silently wrong
  for anything else.
- **`keepers`** — player IDs removed from the pool and pre-assigned to a team's roster counts
  before simming.
- **`roster_size`** — total roster slots, distinct from `num_rounds`, for leagues with
  IR/taxi slots.

`third_round_reversal` lives on `DraftConfig` and is handled by `snake_order()`, but isn't
surfaced as a Draft Manager control until a league actually needs it.

### Replacement level, derived rather than hardcoded

Instead of hand-splitting FLEX across RB/WR/TE by some assumed 45/45/10 share:

1. Take the top `num_teams × (RB + WR + TE + FLEX slots)` skill players by projection as the
   **startable set**.
2. Replacement for each position = the worst startable player *at that position*.

FLEX allocation falls out of the projections instead of a made-up constant, and it adapts
automatically to league size and roster settings.

`replacement_value()` optionally accepts an `available_mask`, which gives a **live**
replacement level mid-draft. This is what makes positional runs genuinely costly: as a
position thins out, its replacement level rises and every remaining player at it gets
comparatively less valuable.

---

## 7. Calibration

### 7.1 Why it's necessary

**The theory:** twelve managers each draw independently and the player goes to whoever rates
him highest, so his draft position is driven by the **minimum** of twelve draws, not the mean.
The minimum of twelve normal draws sits roughly 1.6 standard deviations below centre — which
predicts everyone drifting earlier than their ADP, and high-spread players drifting far more
(stdev 30 → ~48 picks early; stdev 3 → ~5).

**What actually happens, measured 2026-07-31 on the real 237-player pool** (2,000 sims,
uncalibrated, `rho = 0.35`):

```
adp band     players   mean drift   mean input sd
(0, 24]           23        -0.4              2.3
(24, 60]          33        -0.8              5.8
(60, 100]         44        -1.9             10.9
(100, 150]        59        +6.4             14.7
(150, 300]        23        -9.4             25.2
```

Mean |drift| 6.6 picks, median −0.0. **The predicted large monotone early drift does not
appear.** Two reasons the theory overstates it:

1. A draft is a *sequence of argmins over the remaining pool*, not literally "minimum of
   twelve draws per player". The fixed pick count binds hard — exactly 180 players go, so the
   aggregate cannot drift.
2. `rho > 0` reduces disagreement between managers, and disagreement is what drives the
   minimum below the mean.

**The residual error is structural, not the predicted distortion.** It is concentrated at the
deep end and changes sign: the pool holds 237 players for 180 picks, so players with ADP
beyond ~180 can only be drafted *earlier* than their ADP (hence −9.4), which displaces the
100–150 band *later* (hence +6.4). The top 100 players — the ones every real decision concerns
— sit within about 2 picks already.

Calibration therefore has an easy problem, not a hard one. Keep the § 7.3 tolerance honest by
measuring it over players who are actually drafted in most sims.

### 7.2 The loop

A fixed-point loop, with three details that matter:

```python
mu, sd = adp_target.copy(), stdev_target.copy()
for i in range(n_iterations):
    picks = monte_carlo_sim(mu, sd, ..., n_sims=2000, rng=fresh_rng(SEED))   # (1)
    sim_adp   = simulated_mean_pick(picks)
    sim_stdev = simulated_stdev_pick(picks)

    mu += ALPHA * (adp_target - sim_adp)                                     # (2)
    sd[reliable] *= np.clip(
        stdev_target[reliable] / sim_stdev[reliable], 0.8, 1.25              # (3)
    )
    log(i, mean_abs(adp_target - sim_adp), mean_abs(stdev_target - sim_stdev))
```

1. **Common random numbers.** Reseed identically every iteration. Otherwise the
   iteration-to-iteration difference is mostly Monte Carlo noise rather than signal, and the
   loop chases its own variance instead of converging.
2. **Damped update, `ALPHA ≈ 0.7`.** A gain-1 fixed point on a noisy objective oscillates.
   Damping costs a couple of iterations and buys monotone convergence.
3. **Gate BOTH updates on measurability — this is not tidiness, it prevents divergence.**

   `simulated_mean_pick` and `simulated_stdev_pick` are conditional on being
   drafted. For a player taken in only half the simulations, both are measured
   *only in the drafts where he went early* — the runs where he'd have gone later
   record nothing at all. So his simulated mean reads low, the residual reads
   high, `mu` gets pushed later, and he is drafted even less often. **That is a
   positive feedback loop and it diverges.** Observed before the gate was added:
   one boundary player's `mu` climbed 36.9 → 42.4 → 47.3 across three passes
   while his target stayed at 31.

   Restrict **both** the `mu` and `sd` updates to players drafted in >80% of
   simulations. Everyone else keeps `mu = adp_target` and their input `sd` —
   the honest answer, since their target is unreachable in a draft this size and
   the vendor's own measurement beats a statistic the simulation cannot compute.

3b. **Only calibrate `sd` where it's meaningful, and leave the rest alone.**
   `simulated_stdev_pick` is conditional on being drafted, so for deep players the
   distribution is truncated at the `999` sentinel and reads *artificially narrow*. Feeding
   that ratio back would inflate `sd` without bound.

   Restrict the `sd` update to players drafted in >80% of sims. **For everyone else, leave
   `sd` at its input value — do not fit a replacement.** An earlier version of this design
   fitted a `log(sd) ~ log(adp)` curve for those players; that is dropped, because the
   pipeline is deliberately free of any fitted width model (§ 5.5).

   Not updating is the honest move anyway: these are players whose simulated spread cannot be
   measured, and the input value came from FFC's own observation of real drafts, which is
   better evidence than a curve fitted to a different subpopulation. The players affected are
   deep ones who barely influence availability at your actual picks.

`simulated_mean_pick` is deliberately conditional on being drafted, matching how vendors
compute ADP. The comparison is only valid if both sides are defined the same way.

**Measure the trace over a FIXED population.** The per-pass "reliable" set cannot be used,
because its membership changes as calibration moves players in and out of the draft — the
trace would then compare different populations pass to pass and be unreadable. Use players
with `adp_target <= total_picks`, decided once.

**How much calibration buys you scales with how wide the spreads are**, because that is what
drives the min-of-twelve effect. Measured on a synthetic pool:

```
sd = 1 + 0.05·adp    uncalibrated  0.37  ->  calibrated  0.34
sd = 1 + 0.30·adp    uncalibrated  7.21  ->  calibrated  4.02
sd = 1 + 0.60·adp    uncalibrated 30.77  ->  calibrated 17.38
```

With narrow spreads there is nothing to fix and calibration only adds noise. Real FFC data
sits in the regime where it matters: measured 2026-07-31 on the live pool, **4.53 → 0.93
picks** over reliably-drafted players.

Watch both error series settle. **A plateau above ~2 picks means the draft mechanics are
wrong** — almost always `snake_order` or a mutated board — and no amount of further iteration
will fix it.

### 7.3 Validation (`validate_sim`)

Runs before any output reaches a page. These are the difference between wrong numbers caught
in seconds and wrong numbers discovered during a live draft.

1. `mean|simulated_mean_pick - adp_target| < 2.0`
2. `(picks < UNDRAFTED).sum(axis=1) == total_picks` for **every** sim row, not just the max.
   A single short draft means the pool was exhausted or a hard limit locked out the board.
3. No pick number appears twice within a single sim.
4. **Counting identity:** for `k` in `[13, 25, 50, 100, 150]`, the number of players gone by
   pick `k` equals `k - 1`. This is arithmetic, not a modeling assumption, and it requires
   zero data. It loudly catches sign flips, factor-of-two errors, and unit mixups that
   calibration alone will not surface.
5. `snake_order` unit-tested against the hand-written 4-team / 3-round sequence
   `[0,1,2,3,3,2,1,0,0,1,2,3]`, plus the third-round-reversal variant. An off-by-one here
   doesn't crash — it produces a completely plausible draft with systematically wrong
   ownership, and every probability built on top of it is quietly wrong.

---

## 8. Artifacts and execution

`scripts/run_draft_sim.py` runs offline and writes `data/sim/{draft_id}_{config_hash}.npz`:

- **`picks`** — `(n_sims, n_players)` int16. ~1.8 MB compressed at 10,000 × 246.
- **metadata** — the full `DraftConfig`, `random_seed`, `rho`, `platform_weight`, the
  **`ffc_player_id` list in column order** (a matrix without its column ordering is
  uninterpretable — invariant 1), the calibrated `mu`/`sd`, the FFC pull date, and the
  calibration error trace.

`config_hash` covers exactly what the SIMULATION consumes, so a change there mints a new
artifact instead of silently serving a stale one. `data/sim/` is gitignored.
`DraftSimService` loads it behind `@st.cache_resource`, so Streamlit reruns are free.

| In the fingerprint | Deliberately excluded |
|---|---|
| `year`, `num_teams`, `num_rounds` | `draft_position` — decides which picks you *look at* |
| `scoring_format`, `platform` | `starting_slots` — sets replacement level, recomputed on load |
| `keepers`, `third_round_reversal`, `random_seed` | `roster_size` — bookkeeping only |

Everything excluded is recomputed whenever a board loads, so a stale value cannot survive.
Including them would be the safe-looking choice, but it means editing your lineup forces a
full re-simulation that produces a byte-identical matrix.

`platform` **is** included, and adding it was a bug fix: it re-weights the ADP blend, which
moves `adp_target` (measured: mean 2.3 picks, up to 11.8), which changes the draft. Before it
was a field on `DraftConfig` at all, switching platforms silently served the previous
platform's matrix — the model never got a chance to be right or wrong about it.

**Simulations are never triggered by the app.** `scripts/run_draft_sim.py` writes them and
the app only reads. `--list` reports which drafts are current; `--all --skip-existing` tops
up whatever is missing. When no artifact matches, the Draft Plan page shows a warning naming
the command — deliberately loud, because the availability column goes blank BOTH when there
is no simulation and when a player simply isn't in the simulated pool, and those two blanks
would otherwise look identical.

**Do not collapse the matrix into a per-player summary table.** That table is tiny and
extremely tempting, and it destroys `prob_any_available` — joint tier queries cannot be
reconstructed from marginals, because the players are competing for the same picks and their
fates are correlated. Joint queries are the entire payoff of Monte Carlo over the closed-form
curve model. Keep the matrix.

**Undrafted players are handled for free.** Board values are drawn for the whole pool, the
draft consumes `total_picks` of them, and everyone else is simply never taken. No mixture
model, no special-casing — the thing that was awkward in the curve approach is automatic here.

---

## 9. Cost of waiting — the two metrics the page shows

Both read off the picks matrix, and **neither can be reconstructed from per-player marginal
probabilities.** They answer different questions and both are worth showing.

### 9.1 Per-player — "will I lose *this* guy, and does it matter?"

```
cost_of_waiting(player) = P(gone by my_next_pick)
                        × (vorp[player] - E[best at his position surviving to my_next_pick])
```

**The fallback comes from the simulation, not from the current board.** An earlier version of
this section defined it as "the highest-VORP available player at the same position", and that
is degenerate pre-draft — every player is nominally available, so Bijan Robinson's fallback
was Jahmyr Gibbs, who is equally certain to be gone. It returned **0.0 for a 155-VORP back
with a 4% chance of lasting to your pick**, and only the single best-VORP player at each
position ever scored above zero.

Asking the simulation *"what is the best player at this position who actually survives to my
next pick"* fixes that, and behaves identically pre-draft and mid-draft rather than only being
meaningful in one of them. It also makes this the same shape as § 9.2 — both are expectations
over a maximum over survivors, sharing one implementation.

This is the "should I spend this pick on him specifically" number. It's what stops a raw
availability percentage from being misleading: 70% odds of losing someone barely better than
what you'd get anyway is a shrug; 15% odds of losing someone with a cliff behind him is an
emergency.

Measured on the live pool (10 teams, pick 4): Puka Nacua 116.4, McCaffrey 112.4, Gibbs 106.1,
Bijan 87.9 — versus 0.0 for all but four players under the old definition.

### 9.1b Tier survival — and the min/max trap

"Will at least one of these four backs make it back to me?" is a different question from four
separate percentages, and it is the one that actually decides whether you wait.

```python
group = [artifact.column_for(pid) for pid in tier]
survived = artifact.picks[:, group].max(axis=1) >= my_next_pick
return float(survived.mean())
```

**Use `max`, not `min`.** This is worth stating loudly because the original scaffold for this
project got it backwards, and the wrong version produces plausible-looking numbers:

| Expression | What it actually means |
|---|---|
| `picks[:, group].max(axis=1) >= k` | **at least one** of the group is still available |
| `picks[:, group].min(axis=1) >= k` | **every** member of the group is still available |

`min >= k` says the *earliest-drafted* member went at or after k, which can only be true if
none of them went before k. That is "all available" — a far rarer event, and for any tier
containing an early-round player it returns 0% at every pick you care about.

**How much does the joint calculation actually buy you?** Measured 2026-07-31 across 392
non-degenerate tier queries on the real pool, against the independence approximation
`1 - Π(1 - pᵢ)`:

```
mean |difference|   0.6 pp
95th percentile     2.0 pp
maximum             9.2 pp
```

So marginals are usually a decent approximation — this section previously implied otherwise.
The tail is where it matters: 88.6% vs 79.3% on a five-player tier at pick 29 is a materially
different decision. The joint value is always the **higher** one, because the players compete
for the same picks: one going early is precisely what lets another survive, and independence
cannot represent that.

The stronger argument for keeping the matrix is § 9.2, which is an expectation over a
*maximum over survivors* — not approximable from marginals at all. And at 2.18 MB for a
10,000-draft run, the storage question is moot either way.

### 9.2 Per-position — "should I take a RB now, or wait a round?"

At the current pick, for each position, the expected value surrendered by not taking that
position until your next turn:

```
positional_cost_of_waiting(pos) = vorp[best available at pos now]
                                - E_sims[ vorp[best at pos still available at my_next_pick] ]
```

Computed directly off the matrix:

```python
at_pos = (positions == pos) & available_mask              # (P,) candidates right now
survives = picks[:, at_pos] >= my_next_pick               # (S, n_at_pos) per sim
best_later = np.where(survives, vorp[at_pos], -np.inf).max(axis=1)   # (S,)
best_later = np.where(np.isneginf(best_later), 0.0, best_later)      # none left -> replacement
return float(vorp[at_pos].max() - best_later.mean())
```

**Why this is the number that decides between positions**, and not just a sum of § 9.1:

- **It prices the whole tier, not one player.** Eight interchangeable RBs → near-zero cost,
  even though each one individually is probably gone by your next pick. The per-player metric
  cannot see this; it would show eight separate scary-looking numbers.
- **Conversely**, one elite RB with a cliff behind him produces a large cost even at only 40%
  odds of being taken.
- It is an expectation over a **maximum over survivors** — exactly the kind of joint quantity
  marginal probabilities cannot produce, and another reason the raw matrix is kept (§ 8).

Edge cases:

- **Nobody at the position survives in a sim** → that sim contributes `0.0`, which is the
  correct floor: VORP is measured against replacement, so "you get a replacement-level guy"
  is exactly zero.
- **Position already at its hard roster limit** → skip it; the metric is meaningless there.
- **VORP is held at its pre-draft baseline** for this comparison. Replacement level does rise
  as a position thins out, but that effect is already captured by *which players survive*.
  Re-baselining inside the calculation would double-count it.

---

## 10. Build phases

### Phase 1 — FFC data layer
1. `adapters/ffc_adapter.py` — promote and fix the prototype; format/teams mapping;
   raw → canonical shape.
2. `repositories/ffc_repo.py` + `repositories/adp_snapshot_repo.py`, and
   `Collections.FFC_ADP` / `ADP_SNAPSHOTS` in `registry.py`.
3. `scripts/ingest_ffc_history.py` — one-time backfill of all available past years.
   **Run this early — it is the only step that gets harder the longer it waits.**
4. Wire the current-year pull into `scripts/load_data.py` (with the snapshot-before-wipe
   append, if that recommendation is accepted).
5. FFC → `canonical_id` resolution via `PlayerIdentityRepo` with `source="ffc"`; dump
   unresolved names as candidates for manual `player_id_map` rows. Expect K/DST to fail —
   that's by design, not a bug (invariant 5).

### Phase 2 — Sim core
6. `draft_model/config.py` — `DraftConfig`, position constants, `RHO`.
7. `draft_model/mechanics.py` — `snake_order()` **plus its unit tests, before anything else
   depends on it.**
8. `draft_model/table.py` — `blend_adp()`, platform shift, `build_table()`.
9. `draft_model/engine.py` — `draw_boards()` with the shared shock, `sim_batch()` vectorized,
   `monte_carlo_sim()` batching over sims.
10. Smoke run at `n_sims=500`, end to end.
11. Test: vectorized `sim_batch` matches the scalar `effective_value` reference
    implementation on a small fixed-seed case.

### Phase 3 — Calibration
12. `draft_model/calibrate.py` — `calibrate_sampler()` with common random numbers, damping,
    and the reliability-gated `sd` update.
13. `validate_sim()` with all five checks.
14. Full 10k run via `scripts/run_draft_sim.py`; inspect the calibration error trace.

### Phase 4 — Query layer + Draft Plan integration
15. `draft_model/queries.py` — availability, tier survival, derived replacement level, VORP,
    cost of waiting.
16. `services/draft_sim_service.py` — wiring and caching.
17. `pages/draft_plan.py` — availability at each of your picks, with **cost of waiting as the
    headline number.** A raw availability percentage doesn't support a decision on its own:
    70% odds of losing someone barely better than his replacement is a shrug; 15% odds of
    losing someone with a cliff behind him is an emergency.
18. Tier survival: *"84% chance at least one of these four backs gets back to me"* is a
    categorically different piece of information than four separate percentages, and it's the
    one that actually determines whether you wait.

### Phase 5 — In-draft tool (deferred)
19. Live draft log storage + a draft-room page. Nothing tracks live draft state today.
20. `sim_from_state()` — a thin wrapper over `sim_batch` with `start_pick`,
    `already_drafted`, and roster counts **seeded from the real draft log** rather than
    simulated. Horizon of 2 rounds, so the managers picking between your turns apply their own
    positional need. Answering "who's available at my next pick" needs ~11 picks simulated,
    not 150 — comfortably under a second, viable live between picks.

### Phase 6 — Remaining data layer
21. FFB projections trio: rename the current set to Andy's, add Mike's and Jason's, expose
    individual or blended (with average / high / low / spread).
22. Combined FFB service — projections and rankings behind one interface.
23. K/DST from UDK if obtainable.

### Phase 7 — Stdev model: NOT PLANNED
24. **No modeling work.** The only thing that happens on this front is the FFC archiving
    already scheduled in Phase 1 — the raw material kept as insurance. Nothing in the
    pipeline depends on a model existing (§ 5.5), and no code reads the archive.

    If FFC ever actually disappears, `STDEV_MODEL_LONGTERM.md` holds the full method and the
    archive holds the data. Until then this is a problem that has not happened.

### Cross-cutting — start immediately
25. **Prediction logging** (`timestamp, player_id, target_pick, predicted_prob,
    model_version`) and real draft outcome logging (`draft_id, player_id, actual_pick,
    league_settings`). This produces nothing today, which is exactly why it's easy to skip.
    It is the only path to ever scoring this model's calibration against reality — bucket
    predictions by decile and check whether the 70-80% bucket really happened ~75% of the
    time.

---

## 11. Verification

- `pytest tests/test_draft_model.py` — `snake_order` against hand-written sequences (both
  reversal modes); vectorized-vs-scalar agreement on a fixed seed; counting identity on a
  synthetic pool.
- `python scripts/ingest_ffc_history.py --dry-run` — confirm year coverage and row counts
  before writing anything.
- `python scripts/run_draft_sim.py --draft-id <id> --n-sims 500` — end-to-end smoke. The
  calibration trace should show both error series falling monotonically.
- Full 10k run; `validate_sim` must pass all five checks.
- **The eyeball test no assertion replaces:** pick 8–10 players you have strong intuitions
  about and check their simulated availability at your picks. If Bijan is 30% available at
  pick 20, something is wrong regardless of what the assertions say.
- `streamlit run streamlit_app.py` → Draft Plan page: availability and cost-of-waiting
  columns populate, and the page stays responsive (artifact loads from cache; no sim runs on
  rerun).

---

## 12. Constants and open items

Every number below is a judgment call, not something the data chose. Listed explicitly so
they don't get mistaken later for fitted values. These are the starting values for
`draft_model/config.py`.

```python
UNDRAFTED = 999          # sentinel; must exceed any real pick number (invariant 3)
RHO       = 0.35         # manager agreement within a draft (§ 3.2)
ALPHA     = 0.7          # calibration damping (§ 7.2)
NEED_BONUS = 15.0        # board-value units a manager reaches past to fill a starter slot
BLOCK      = 10_000.0    # added to a roster-full player's value; effectively unpickable

HARD_LIMIT       = {"QB": 2,   "RB": 6,  "WR": 6,  "TE": 2,   "K": 1,   "DST": 1}
STARTER_DEADLINE = {"QB": 100, "RB": 60, "WR": 60, "TE": 100, "K": 170, "DST": 170}

PLATFORM_WEIGHT = 0.5    # 0.0 = pure FFC ADP, 1.0 = pure platform blend (§ 5.3)
POOL_MULTIPLIER = 1.5    # drop players with adp_target > total_picks * this
```

- **`RHO = 0.35`** — unfittable, for the reasons in § 3.2. Revisit after a season of logged
  drafts.
- **`PLATFORM_WEIGHT = 0.5`** — worth a sensitivity check: if sweeping it from 0.0 to 1.0
  barely moves the availability numbers, the whole shift mechanism isn't earning its
  complexity and should be cut.
- **`STARTER_DEADLINE` and `NEED_BONUS`** control how strongly positional runs emerge. Runs
  are an *emergent* property of these two constants, not something programmed anywhere: once
  two managers take tight ends, the remaining TE-less managers start applying the bonus and
  reaching, and the clustering appears on its own. Once the sim runs, compare simulated run
  frequency against real draft logs and tune.
- **`HARD_LIMIT`** — max players a simulated manager will roster per position. Too tight and
  the board can lock up (validation check 2 catches this); too loose and rosters stop looking
  like real ones.
- **`STARTER_DEADLINE` for K/DST** is doing real work now that they're in the pool — it's
  what stops the sim from drafting kickers in round 8.

### `DraftConfig` fields

Frozen dataclass, passed to every function that needs league context rather than threading
loose parameters — which prevents the classic bug where the sim runs 12-team but the query
assumes 10-team.

```
year, num_teams, num_rounds, scoring_format, my_picks, starting_slots,
keepers, roster_size, third_round_reversal, random_seed
total_picks (property) = num_teams * num_rounds
```

`my_picks` comes from `DraftPlanService.pick_labels()` and is the only set of picks any
Draft Plan output is ever evaluated at — in a snake draft that's ~15 numbers, not 200.

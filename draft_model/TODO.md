# Draft Model — TODO

Build checklist for `DESIGN.md`. Section references (§) point there.

Items are checked off as they land, and a phase gets ✅ when it's done. Anything that deviates
from the original plan — an extra step, a skipped one, a limitation discovered on the way —
gets noted inline rather than silently absorbed, so the checklist stays an honest record of
what was actually built.

Phases are ordered by dependency. **One item is time-sensitive**: the FFC historical backfill.
Those seasons are available today and gone whenever FFC changes. Everything else can wait.

*(Platform ADP archiving was also time-sensitive and has been deliberately deferred — see the
end of Phase 1. The cost of that choice is recorded in § 5.5 rather than left implied.)*

---

## Phase 0 — Housekeeping ✅

- [x] Add `requests`, `numpy`, `pytest` to `requirements.txt`
      (`requests` and `numpy` were already used transitively; now explicit)
    - [x] Dropped `shiny==1.5.0` — nothing outside `streamlit_poc/` imports it and there is
          no `app.py`. Note: still installed in the local env; only a fresh install drops it
- [x] Add `data/sim/` to `.gitignore` (generated artifacts, ~6 MB each) — plus
      `.pytest_cache/`
- [x] Create `tests/` with `__init__.py` — no test suite existed
- [x] `pytest.ini` with `pythonpath = .` and `testpaths = tests` *(not originally listed)*.
      Without it, whether `import draft_model.mechanics` resolves depends on pytest's
      import-mode heuristics — works by accident today, breaks confusingly at the first
      Phase 2 test
- [x] Fix `CLAUDE.md` run command: `shiny run ... app.py` → `streamlit run streamlit_app.py`,
      and document `pytest`

**Verified:** pytest 8.3.4 picks up the config and collects cleanly (0 tests, as expected);
`numpy` 2.1.3 and `requests` 2.32.3 import.

**Left alone, noted:** `streamlit_poc/` still holds two Shiny-era files
(`adp_comparison_app.py`, `draft_plan_app.py`) — now the only code referencing a dependency
we no longer declare.

---

## Phase 1 — Data layer: FFC + ADP snapshots

The only hard blocker: FFC is the sole source of draft-position spread. The historical
backfill below is the only *time-sensitive* work in the whole plan.

- [x] **Probe the live endpoint before writing any parser** — done 2026-07-30. Findings
      folded into § 5.2; three of them contradict what the API's shape implies
- [x] `adapters/ffc_adapter.py` — promote `fantasy_football_calculator.py` (§ 5.2)
    - [x] **Fixed the bug:** prototype hardcoded `params={"teams": 12, "year": 2026}`
          while accepting `teams`/`year` as arguments
    - [x] `ScoringFormat` → FFC format mapping (`REGULAR`→`standard`, `HALF_PPR`→`half-ppr`,
          `FULL_PPR`→`ppr`). `dynasty`/`rookie` return zero players — not offered
    - [x] **Position mapping `PK`→`K` and `DEF`→`DST`**, centralized as
          `registry.POSITION_ALIASES` + `canonical_position()`; `parse_positions()`
          refactored to use it so aliasing lives in one table
    - [x] Pass a valid `teams` (8/10/12/14) to avoid HTTP 400, documented as cosmetic —
          verified identical ADP across all four
    - [x] Surface the `meta` block (`total_drafts`, `start_date`, `end_date`, `rounds`)
    - [x] Raw → canonical shape: `ffc_player_id, name, position, team, adp, stdev, high,
          low, times_drafted, bye`. Column is `position`, matching the other adapters —
          DESIGN § 5.4 updated from `pos` to match
    - [x] `stdev == 0` → **NaN rather than a floor** *(deviation from the original item)*.
          A floor is a modeling decision and this file is a shape boundary; 0 means
          "unmeasurable", not "no spread", so NaN forces `table.py` to fall back explicitly
    - [x] `fetch()` returns an `FfcPull` result object instead of raising *(not originally
          specified)*. Collapses both no-data signals into one `.ok` flag so a year loop
          cannot handle only one of them — the exact bug that would lose 2010–2024
    - [x] `adp_formatted` dropped — it bakes in FFC's assumed 12 teams;
          `ui_helpers.adp_to_round_pick()` computes it against your real league size
- [x] `tests/test_ffc_adapter.py` — 4 offline tests locking in the position remap, the
      stdev→NaN rule, adp sorting, and team-count snapping

**Verified live:** 2026 PPR returns 246 rows; positions
`['DST','K','QB','RB','TE','WR']`; exactly 1 stdev NaN; half-PPR 204 rows; 2025 and 1998 both
`ok=False` with distinct messages. `pytest` 4 passed.
- [x] `Collections.FFC_ADP` + `Collections.ADP_SNAPSHOTS` in `registry.py`
- [x] `db/documents.py`: `bulk_upsert()` + `ensure_index()` *(not originally listed)*.
      The existing `upsert()` is one round trip per document — the backfill would be minutes
      of pure latency, and the key lookup needs an index or the job gets slower as it grows
- [x] `repositories/ffc_repo.py` — reads `ffc_adp` (current state)
- [x] `repositories/adp_snapshot_repo.py` — reads/appends `adp_snapshots` (source-agnostic
      by design; only FFC actually populates it — see the deferral at the end of this phase)
    - [x] `compute_content_hash()` + skip-if-unchanged guard
    - [x] `ensure_indexes()` — unique on the key, plus a per-player history index
    - [x] `read()` / `coverage()`

**Verified against the live DB** (2026-07-30): unique index enforced (a raw duplicate insert
is rejected by Mongo, not silently accepted); re-running an unchanged payload writes 0 rows;
a forced re-write reports `matched=246, upserted=0` with the document count unchanged —
genuinely idempotent; `stdev` NaN survives the round trip.

**First real snapshot stored:** ffc / 2026 / ppr / 2026-07-30, 246 rows, from a window of
3,796 drafts. *(Deviation: `snapshot_date` uses `meta.end_date`, not `start_date` as the item
below originally said — end_date is the "as of" date, the last day of drafts feeding that ADP,
and it's the right choice for the rolling current-season window as well as for history.)*
- [x] `scripts/ingest_ffc_history.py` — **one-time backfill, DONE 2026-07-30** (§ 5.1)
    - [x] `--dry-run` flag: reports year coverage and row counts before writing
    - [x] **Scope: 2020–2024**, 3 formats. *(Deviation: dropped `2qb` — it has no
          `ScoringFormat` value, so including it meant bypassing the app's scoring
          vocabulary for a format the app can't express. One-line addition if ever needed.)*
    - [x] Iterates the range and skips empties rather than walking back until failure
    - [x] Handles both no-data signals via `FfcPull.ok`
    - [x] `meta.end_date` as `snapshot_date`
    - [x] Writes into `adp_snapshots`, idempotent

**Backfill result:** 15/15 pulls succeeded, **2,926 rows written**, all clean inserts
(`updated 0` everywhere — no key collisions). `adp_snapshots` now holds 16 snapshots.
Every historical window is 2–7 days at season start, confirming § 5.2: each season is one
datapoint per format, not a time series.

- [x] Current-year FFC pull wired into `scripts/load_data.py`
    - [x] Wipe-and-replace `ffc_adp`, matching the existing collection paradigm
    - [x] **All three formats stored together with a `format` column** *(not originally
          specified)* — scoring format is a per-draft setting and FFC returns a genuinely
          different pool for each (246/204/186). `FfcRepo.current(fmt)` filters
    - [x] Also appends the pull to `adp_snapshots`
    - [x] `--skip-ffc` and `--year` flags; a failed pull is reported and skipped rather
          than raised, so a network outage can't stop the CSVs loading

**Verified:** `load_data` loaded 636 FFC rows across 3 formats. The content-hash guard fired
for real — `full_ppr` reported *"unchanged, not re-archived"* because that exact snapshot was
already stored during earlier verification.

- [x] FFC → `canonical_id` resolution — `services/ffc_service.py`, wired into `AppContext`
    - [x] `with_canonical_id(fmt)` — nullable enrichment, **never a filter**. Unresolved
          players still occupy picks in the sim (invariant 5)
    - [x] `resolution_report(fmt)` splits misses into *expected* (team defenses) and
          *actionable* (skill players worth a `player_id_map` row)
    - [x] Only `DST` is classed unresolvable-by-design — **verified kickers resolve fine**
          (18/19), so lumping them in would have been wrong

**Resolution rates (full_ppr):** QB 100%, WR 96.4%, K 94.7%, RB 93.7%, TE 91.7%, DST 0%
(by construction). 8–10 actionable misses per format — see the identity issue below.

### Player identity normalization — fixed in passing (2026-07-30)

Surfaced by the FFC resolution check, but **a pre-existing app-wide bug**, not something the
draft model introduced. `PlayerDirectory.resolve_by_display_name()` lowercased and trimmed but
handled no other variation, so generational suffixes, accents and apostrophes all failed to
match. Because `RosterService.roster()` drops rows without a `canonical_id`, those players
vanished from the app entirely — Player Profile, ADP Comparison, Draft Plan, everything.

**Worst case found: James Cook III — RB5 overall, ADP round 1 pick 10 — was not in the app.**

- [x] `normalize_name()` in `repositories/player_directory.py` — strips accents, punctuation
      and generational suffixes (`Jr`/`Sr`/`II`/`III`/`IV`). Bare `V` deliberately excluded:
      a lone letter is more likely part of a real name, and a wrong strip beats a missed one
- [x] **Two-pass matching, strictly additive.** Exact match runs first, unchanged;
      normalization only sees names that already failed. No previously-correct match can be
      altered by this change
- [x] **Ambiguous keys resolve to nothing, never to a guess.** If two players share a
      normalized name *and* position (261 such rows exist — e.g. two Adrian Petersons, both
      RB), that key is excluded and both stay unresolved. Silently attaching the wrong
      `gsis_id` is an error that would never surface
- [x] `name_collisions(with_position=True)` diagnostic, mirroring the real lookup key
- [x] Manual `player_id_map` rows for the two cases normalization cannot reach:
    - **Travis Hunter** — in nflreadpy at CB (genuine two-way player); every fantasy source
      says WR, so name matches but position doesn't
    - **Hollywood Brown** — nickname for Marquise Brown; four sources use the nickname
- [x] `PlayerIdentityRepo` dtype fix — assigning ids into an all-NaN float column is a
      pandas 2 `FutureWarning` and a pandas 3 error
- [x] `tests/test_player_directory.py` — 6 tests, including that this is NOT fuzzy matching
      ("Mike Williams" must not match "Michael Williams")

**Result — every source improved, not just FFC:**

| Source | Unresolved before | After |
|---|---|---|
| udk | 10 | **0** |
| ffb | 11 | 1 |
| espn | 15 | 5 |
| sleeper | 41 | 10 |
| yahoo | 140 | 112 |

`RosterService.roster()`: **297 → 307 of 307**. FFC actionable misses: 0 in all three formats.
(Remaining espn/sleeper/yahoo misses are players outside the UDK universe, filtered out anyway.)

### Platform ADP archiving — DEFERRED (decision 2026-07-30)

**Not being built.** ESPN/Yahoo/Sleeper ADP is not archived; `adp_snapshots` holds FFC only.

Reasoning: platform ADP arrives via manual CSV scrapes (ESPN and Sleeper have Python
scrapers; **Yahoo is a browser console script and cannot be automated as-is**), so the archive
would only ever be as regular as the scraping habit — and an irregular series carries much of
the cost with little of the benefit.

**What this costs, recorded so it isn't rediscovered as a mystery** (full version in § 5.5):

- Phase 7's austere model will be **trained on FFC ADP and deployed on platform ADP**. That
  is train/serve skew of unknown size, because there's no platform history to measure it
  against. It must be written into the model's text artifact as a known limitation.
- **ADP volatility is unavailable**, not merely hard. `STDEV_MODEL_LONGTERM.md` calls it the
  strongest durable predictor; the backfill cannot supply it (§ 5.2 — historical rows are
  single 2–4 day windows per season). Build the first model without it.
- The insurance still has value — degrading from a skewed estimate to a worse one beats
  having no width estimate at all — but it is weaker than the design originally assumed.

**Reversible at any time**, at the cost of the history not collected in the meantime. If this
gets revisited, the collection schema and the `content_hash` guard already support it; only
`scripts/snapshot_adp.py` and the `load_data` hook are missing.

- [ ] ~~`scripts/snapshot_adp.py`~~ — deferred
- [ ] ~~Call it from `scripts/load_data.py`~~ — deferred

---

## Phase 2 — Sim core

- [x] Extend `DraftService` + `pages/draft_manager.py` with the three new settings (§ 6)
    - [x] `starting_slots` — sets the VORP replacement level; a lineup editor row on the
          Draft Manager page
    - [x] `keepers` — multiselect of canonical_ids, removed from the pool before simulating.
          *Limitation: a kept team defense can't be represented (defenses have no
          canonical_id). Accepted — vanishingly rare, costs one pick of accuracy*
    - [x] `roster_size` — total slots, distinct from `num_rounds`
    - [x] Old drafts saved before these fields keep working (`from_draft_doc` uses `.get()`)
    - [x] Live "Your picks" caption from `picks_for_slot`, so the league shape is verifiable
          at a glance rather than after a simulation

**Two bugs fixed while wiring this up:**
- **Pre-existing crash on "Save changes"** — the draft picker was keyed by *name*, so a rename
  orphaned its stored selection, which the page patched by writing `session_state` after the
  widget already existed. Streamlit forbids that. Now keyed by `draft_id` (stable across
  renames) with `format_func` for display, so the workaround is unnecessary rather than fixed
- **Performance regression the keeper picker introduced** — `roster_service.player_names()`
  costs 0.35s per call and Streamlit reruns the whole script on any widget change, so every
  keystroke would have paid it. Wrapped in `@st.cache_data`

**Verified round-trip:** a draft saved with custom slots, two keepers and `roster_size=20`
rebuilds into a `DraftConfig` with the right `total_picks` and `my_picks`, and a **different
fingerprint** from the default-slots version — so a lineup change invalidates a cached
simulation instead of silently reusing it.

- [x] `draft_model/config.py` — `DraftConfig`, constants, `RHO` (§ 12)
    - [x] *(Deviation: `my_picks` is a derived property from `draft_position`, not a stored
          field as § 12 sketched. A stored copy can drift out of sync with num_teams /
          num_rounds — the exact disagreement DraftConfig exists to prevent.)*
    - [x] `fingerprint()` added now *(not originally in Phase 2)* — § 8 needs a config_hash
          so a settings change mints a new artifact instead of serving a stale one
    - [x] `__post_init__` validation; `from_draft_doc()` tolerates drafts saved before
          `starting_slots`/`keepers`/`roster_size` existed
- [x] `draft_model/mechanics.py`
    - [x] `snake_order(pick_num, num_teams, third_round_reversal)`
    - [x] `picks_for_slot()` — derived FROM snake_order rather than given its own arithmetic,
          so the two can't drift apart
    - [x] `effective_value()` — scalar reference implementation, tests only, not the hot path
    - [x] **Unit tested before anything depends on it.** 4-team/3-round hand sequence,
          third-round reversal, and the structural invariant that every team appears exactly
          once per round across 5 league sizes

**Verified independently of the tests:** rebuilt the pick order by alternating a list (the
physical description of a snake) and compared to the formula across 20 combinations of team
count × round count × reversal — all agree. Conservation holds: 180 picks, none owned twice,
full 1..180 coverage, 15 per team. Slot 5 of 12 → `(5, 20, 29, 44, ...)`, gaps alternating
15/9. **21 tests passing.**

- [x] `draft_model/table.py`
    - [x] `blend_adp(sources, weights)` — weights renormalized **per player**, so a deep
          player present only in Sleeper gets Sleeper's ADP, not a fifth of it
    - [x] `apply_platform_shift()` — `adp_target = ffc + w * (platform - ffc)`; unmatched
          players keep their FFC value rather than becoming NaN
    - [x] `fill_missing_stdev()` — the three-step chain (§ 5.2), **no fitted model**:
          FFC stdev → `(high-low)/4` → median of 20 nearest-ADP **same-position** players,
          with a `MIN_STDEV` floor so a zero width can never make a player deterministic
    - [x] Thin-sample players deliberately left alone
    - [x] `build_table()` — pool cap, enrichments joined by canonical_id (never a filter),
          `mu`/`sd` seeded from targets, row order frozen as picks-matrix column order
    - [x] **Bug caught during integration:** validation originally ran *after* the pool cap,
          but `NaN <= cap` is False in pandas — a player with missing ADP was silently
          dropped by the cap instead of raising. Validation now runs first
- [x] `tests/test_table.py` — 16 tests

**First real run** (2026 full-PPR, 246 FFC players → 237 after the cap): every invariant
holds — index contiguous, sorted by `adp_target`, no NaNs, all `stdev_target > 0`,
`ffc_player_id` unique, all six positions present, 27 rows correctly kept without a
`canonical_id`. **The stdev fallback fired exactly once** — Zachariah Branch → 14.20 from the
nearby-WR neighbourhood, matching the 15.00 predicted before the code existed. Platform shift
moved 188/237 players, mean 6.3 picks, max 28.2.

**Projection coverage 186/237**, and the gaps are understood rather than mysterious:
K (19) and DST (27) have no projections at all — fine for the sim, but **Phase 4's
`compute_vorp()` must handle NaN projections**, since VORP is undefined for them. Of the 5
skill players missing one, `Kenneth Gainwell` was another nickname mismatch (fixed with a
`player_id_map` row; ffb unresolved is now empty); the rest are FFC-ranked players outside the
UDK universe, so no projection exists to find.

- [x] `draft_model/engine.py`
    - [x] `draw_boards()` — shared-consensus shock + idiosyncratic noise (§ 3.2), drawn once
          per simulated draft, never per pick
    - [x] `sim_batch()` — vectorized across sims, one numpy op per pick (§ 3.1). Takes
          **pre-drawn boards** as an argument *(not originally specified)*, which is what
          makes the vectorized-vs-scalar test possible: both implementations get identical
          boards and must produce identical drafts, not merely similar statistics
    - [x] `sim_one_draft_reference()` — the obvious slow version the fast one is proven equal to
    - [x] `monte_carlo_sim()` — batches over sims to cap memory
    - [x] `position_index()` / `position_limit_arrays()` — positions as integers so per-position
          constants become arrays the hot loop can index
    - [x] Pool-too-small guard: `argmin` over an all-infinite row returns 0, quietly
          "drafting" someone already taken. Raises instead
    - [x] Did **not** build the pointer-walk or 15-player-window optimizations
    - [x] `start_pick` / `end_pick` / `already_drafted` / `roster_counts` hooks in place, so
          Phase 5's `sim_from_state` is a thin wrapper
- [x] `tests/test_engine.py` — 15 tests

**Bug found by the test suite:** `batch_size` silently changed results. `draw_boards` consumed
the RNG stream sequentially (all `shared`, then all `private`), so regrouping the same
simulations into different batch sizes assigned different random numbers to different players.
That made reproducibility depend on a memory-tuning knob — somebody lowering `batch_size` to
fit a smaller machine would have changed their draft advice with no indication.

Fixed with `draw_boards_for_sims()`: simulation *s* draws from a stream keyed by
`(seed, s)`, so its boards are a pure function of those two values. **Verified identical
across `batch_size` 1/4/6/24**, and any single simulation can now be regenerated in isolation
— which is how you investigate "why did simulation 4,732 do that?" without re-running the
other 9,999. Cost is negligible (0.03s of seeding per 2,000 sims).

**Measured on the real 237-player pool (2026 full-PPR):**
- **0.2 ms per draft — ~2 seconds for 10,000 drafts.** Far better than DESIGN's original
  tens-of-seconds estimate; § 3.1 updated
- Counting identity holds **exactly** at picks 13/25/50/100/150 across every simulation
- Uncalibrated drift is much smaller than § 7.1's theory predicted: mean |drift| 6.6 picks,
  median −0.0, and the top 100 players are already within ~2. The residual is a pool-size
  effect at the deep end (237 players for 180 picks), not the predicted differential
  distortion. § 7.1 rewritten with the measured numbers

- [x] `draft_model/artifacts.py` — save/load `.npz` + metadata (§ 8)
    - [x] Metadata includes the `ffc_player_id` list in column order, and a **missing one is
          a hard load error** — a matrix whose columns can't be identified still produces
          numbers, which is worse than not loading
    - [x] `column_for()` — the only correct way to index the matrix by player; raises rather
          than returning something plausible for an unknown id
    - [x] `artifact_path()` embeds `config.fingerprint()`, so a settings change is a cache
          miss rather than a stale hit
    - [x] `matches_table()` — **order-sensitive**, not membership. Catches DATA changes (a
          newer FFC pull re-sorting the pool), which the fingerprint knows nothing about
    - [x] Config stored as plain JSON, not a pickle — readable in ten years with `np.load`
- [x] `tests/test_artifacts.py` — 9 tests

- [x] Smoke run at `n_sims=500`, end to end — ran clean on the real 237-player pool
- [x] **Full end-to-end run (2026-07-31):** live FFC → table → 10,000 drafts → artifact →
      reload → availability. **1.9 seconds** for 10,000 drafts (0.19 ms each); artifact is
      2.18 MB on disk (4.7 MB in memory); reloads byte-identical and matches the current
      table. Availability curves are sensible — Bijan/Gibbs ~0% available at pick 5,
      McCaffrey 81%, and the coin-flip players at each of your picks are plausible names
- [x] Test: vectorized `sim_batch` matches the scalar `effective_value` path on a fixed seed
      (`test_vectorized_matches_reference`, plus a 12-team/6-round variant that exercises the
      snake turns and starter deadlines). Not "statistically similar" — byte-identical drafts

---

## Phase 3 — Calibration

- [x] `draft_model/calibrate.py` — `calibrate_sampler()` (§ 7.2)
    - [x] Common random numbers — comes free from per-simulation seeding, so each pass
          re-simulates with identical draws and only `mu`/`sd` differ
    - [x] Damped update, `ALPHA = 0.7`
    - [x] **Both** `mu` and `sd` updates gated on players drafted in >80% of sims. The rest
          keep their input values rather than being fitted to a statistic that cannot be
          measured for them
    - [x] Trace measured over a **fixed** population (`adp_target <= total_picks`) so passes
          are comparable
- [x] `simulated_mean_pick` / `simulated_stdev_pick` / `prob_undrafted` / `draft_rate` — all
      masking the `999` sentinel, all conditional on being drafted (matching how vendors
      compute ADP)
- [x] `validate_sim()` — all five checks (§ 7.3), reporting calibration error over **two**
      populations so the gate can't be mistaken for the whole story
- [x] `scripts/run_draft_sim.py` — `--list` (shows which drafts have a CURRENT run),
      `--all`, `--skip-existing`, `--draft-id`, `--dry-run`, `--no-calibrate`, `--n-sims`;
      refuses to save an artifact that fails validation, and `--all` keeps going if one
      draft fails rather than aborting the batch
- [x] `tests/test_calibrate.py` — 12 tests
- [x] Full 10k run; calibration trace inspected

**Two bugs the tests caught, both real:**

1. **`simulated_stdev_pick` returned `0.0` for a player drafted exactly once.** `np.nanstd`
   does that with one observation, but 0.0 reads as "zero spread" when it means
   "unmeasurable" — the same trap as FFC's `stdev=0`, and it would make calibration chase a
   target of zero. Now returns NaN below two observations, matching its docstring.
2. **`mu` diverged for boundary players.** The `sd` update was gated on reliability but `mu`
   was not. For a player drafted half the time, `sim_adp` is conditional on the drafts where
   he went *early*, so the residual reads high, `mu` gets pushed later, and he's drafted even
   less — positive feedback. One player's `mu` climbed 36.9 → 42.4 → 47.3 while his target
   stayed at 31. Gating both updates fixed it, and improved the real validation error from
   **1.47 → 0.93 picks**. Regression test added.

**Also established:** calibration's value scales with spread width (uncalibrated → calibrated
is 0.37 → 0.34 at narrow spreads, 30.77 → 17.38 at wide ones). A test asserting "calibration
always reduces error" was wrong — with narrow spreads there is nothing to fix. The test now
uses a wide-spread pool, which is the regime real FFC data occupies.

**Live run (2026-07-31), 10 teams / pick 4 / 15 rounds / full PPR:**
- Calibration 4.53 → 0.93 picks over 126 reliably-drafted players (tolerance 2.0)
- 2.23 over all 161 expected-drafted, the difference being boundary players whose targets are
  unreachable — 237 players compete for 150 picks, so anyone with ADP past the last pick can
  only ever be drafted *earlier* than his ADP
- All five validation checks pass; artifact 1.82 MB

## Phase 4 — Query layer + Draft Plan integration

- [x] `draft_model/queries.py`
    - [x] `prob_available_at_pick()`, `availability_matrix()` (whole grid in one pass)
    - [x] `prob_any_available()` — tier survival, using **max** (§ 9.1b). `prob_all_available()`
          added alongside so the `min` form has an honest name and can't be mistaken for it
    - [x] `replacement_value()` — **derived** from `starting_slots`, with FLEX allocated by
          projection rather than an assumed 45/45/10 split
    - [x] `compute_vorp()` — NaN propagates deliberately; K/DST have no projections and a
          fabricated zero would make them look replacement-level rather than unknown
    - [x] `cost_of_waiting()`, `positional_cost_of_waiting()`, sharing one
          `expected_best_at_pick()` core
- [x] `services/draft_sim_service.py` — `DraftBoard` bundling table + artifact + VORP already
      aligned, so the column-order invariant isn't re-established at every call site
    - [x] `build_model_table()` **shared with `scripts/run_draft_sim.py`**, which now calls it
          rather than keeping a second copy that could drift
- [x] Register in `AppContext.__init__`
- [x] `pages/draft_plan.py`
    - [x] Availability at the selected round's pick, as a progress-bar column
    - [x] **Cost of waiting per position** as the headline metric row
    - [x] Tier survival on each position board — your shortlist for a round *is* a tier
    - [x] `@st.cache_resource` on the artifact load, so reruns don't re-read it
    - [x] Missing simulation shows the command to fix it; a stale artifact warns

**Two definition bugs caught by looking at real output rather than tests:**

1. **`cost_of_waiting` returned 0.0 for almost everyone.** DESIGN § 9.1 defined the fallback as
   "the highest-VORP available player at the same position", which is degenerate pre-draft —
   Bijan Robinson's fallback was Jahmyr Gibbs, equally certain to be gone. A 155-VORP back with
   a 4% chance of lasting scored zero. The fallback now comes from the **simulation**: the best
   player at that position who actually survives to your next pick. Works identically pre-draft
   and mid-draft. Real numbers went from "0.0 for all but four players" to Nacua 116, McCaffrey
   112, Gibbs 106, Bijan 88.
2. **`positional_cost_of_waiting` priced players you could never have had.** It used the global
   best VORP at a position as "best available now", so evaluating round 3 it reported the cost
   of losing McCaffrey — who is 0% available at pick 24. Both sides now come from the
   simulation when there's no live board.

**The output is now decision-shaped.** Positional cost by round (10 teams, pick 4):

```
 round  pick -> next     QB    RB    WR    TE
  1.04     4 ->   17      5   110    96     0
  2.07    17 ->   24     12    13     3     4
  3.04    24 ->   37     24    17     6    43      <- the elite TE cliff
  4.07    37 ->   44      4     7     3    14
```

**Verified:** 93 tests pass; app starts clean with no errors.

## Phase 5 — In-draft tool (deferred)

Nothing tracks live draft state today; this is effectively a second feature.

- [ ] Live draft log storage (collection + service)
- [ ] Draft-room page for entering picks as they happen
- [ ] `sim_from_state()` — thin wrapper over `sim_batch`, roster counts seeded from the
      **real** log rather than simulated
- [ ] Horizon of 2 rounds — lets managers picking between your turns apply their own
      positional need. ~11 picks, not 150; comfortably sub-second

---

## Phase 6 — Remaining data layer ✅

Split deliberately: **you gather the files, I wire them up.** Everything in "Data to
gather" is manual export work; nothing below it can start until those land, because the
column shapes decide the adapter.

Run `python scripts/check_data_files.py` at any point — it validates names, locations and
**column order** without touching the database, and tells you exactly what's still missing.

### Data to gather — yours

**Fantasy Footballers projections, three analysts.** Six files total, all in `data/ffb/`:

- [ ] Rename `ffb_qb_projections.csv` → `ffb_qb_projections_andy.csv`
- [ ] Rename `ffb_flex_projections.csv` → `ffb_flex_projections_andy.csv`
- [ ] Export `ffb_qb_projections_mike.csv` and `ffb_flex_projections_mike.csv`
- [ ] Export `ffb_qb_projections_jason.csv` and `ffb_flex_projections_jason.csv`

Required header order — **exact, including the duplicates**:

```
QB   : Name, Team, Bye Week, Rank, PPG, YDS, TDS, YDS, TDS, INT, FUM
Flex : Name, Team, Bye Week, Pos, Rank, PPG, ATTS, YDS, TDS, REC, YDS, TDS, FUM
```

> **Why order matters more than usual here.** `YDS` and `TDS` each appear twice — rushing
> then receiving. pandas renames the second occurrence to `YDS.1`/`TDS.1` and the adapter
> maps them **by position**. A file with the right columns in a different order loads with
> no error at all and attributes receiving yards to rushing. The validator checks for this
> case specifically and says so.

**UDK K/DST rankings — optional.** Same 13-column schema as the existing UDK files:

- [ ] `data/ffb/udk_k_rankings_ppr.csv`
- [ ] `data/ffb/udk_dst_rankings_ppr.csv`

If UDK publishes these with different columns (no Risk/Upside, say), don't force them into
the existing shape — hand them over as-is and the adapter gets a branch.

**After the files are in place:**

- [ ] `python scripts/check_data_files.py` — everything reads `ok`
- [ ] `python scripts/load_data.py`
- [ ] **Drop the orphaned collections.** `load_data` only wipes collections it finds a CSV
      for, so after the rename `ffb_qb_projections` and `ffb_flex_projections` linger in
      Mongo holding stale data. I'll add this to the load script, or drop them by hand.

### Wiring — done 2026-07-31

- [x] `Collections` entries: `FFB_QB_PROJECTIONS` / `FFB_FLEX_PROJECTIONS` are now
      `{analyst}` templates; `UDK_K_RANKINGS` / `UDK_DST_RANKINGS` added
- [x] `registry.POSITION_ALIASES` gains **`D` → `DST`** — UDK's defense export uses `D`,
      a third spelling alongside FFC's `DEF` and our canonical `DST`
- [x] `FfbProjectionsAdapter` takes a repo pair per analyst; `load(analyst)` and
      `load_all()` (long format, one row per player-analyst)
- [x] `ProjectionsService.get_own_projections()` blends by default, or takes an analyst
- [x] Per-format `_low` / `_high` / `_spread` and `n_analysts` on the blend
- [x] `ProjectionsService.disagreement(fmt)` — the new signal
- [x] Stale `ffb_qb_projections` / `ffb_flex_projections` CSVs deleted and the orphaned
      Mongo collections dropped (297 stale docs)
- [x] `tests/test_projections_blend.py` — 6 tests
- [x] Simulation re-run on blended projections; all validation checks still pass

**Verified:** 307 blended players, **zero unresolved names** across all three analysts
(the suffix/accent normalization from Phase 1 is doing the work). 291 players rated by all
three, 11 by two, 5 by one. The blend sits within each player's [min, max] in every case.

**Bug caught by a downstream check:** `bye_week` is numeric but is an *identity* column, so
it landed in both the numeric aggregation and the label frame and the merge renamed both to
`bye_week_x` / `bye_week_y`. Nothing raised — the column consumers expect simply ceased to
exist. Regression test added.

**The disagreement signal is real and interpretable.** Widest spreads in full-PPR season
points: Tua Tagovailoa **87 → 231**, Michael Penix Jr. **0 → 143**, Jordan James 22 → 143.
All players whose role is genuinely unsettled — which is exactly what the measure should
surface, and it is independent of ADP spread.

### K/DST — loaded, deliberately outside the player universe

Decision 2026-07-31. The files turned out to be **rankings, not projections**: a consensus
`Rank` plus each analyst's own rank, no points at all. So no VORP is possible for these
positions regardless — the earlier concern about kickers acquiring a cost-of-waiting does
not apply.

- [x] Loaded into `udk_k_rankings_ppr` (33) and `udk_dst_rankings_ppr` (32)
- [x] `scripts/check_data_files.py` knows their distinct schema
- [ ] NOT added to `RosterService`. Adding them means NaN in every roster column they lack
      (points, risk, upside, ADP, tier) and noise in ADP Comparison and Player Profile. The
      draft model already handles K/DST from FFC and does not need these. Revisit if kicker
      or defense guidance is ever wanted.

## Phase 7 — Stdev model: NOT PLANNED (decision 2026-07-30)

**No modeling work is planned.** Not deferred-with-intent — genuinely not on the list until
the problem actually occurs.

**What IS happening, and it's the whole of it:** FFC's raw `adp` / `stdev` / `high` / `low` /
`times_drafted` gets archived into `adp_snapshots` (2020–2024 backfill + current season on
every `load_data`). That archive is the *option* to build an estimator later. No code reads
it. Nothing depends on it.

- [x] Confirmed nothing in the pipeline requires a fitted width model:
    - [x] § 5.2 unusable `stdev` → median of 20 nearest-ADP same-position players, computed
          fresh from the same table. No training, no artifact, no history
    - [x] § 7.2 calibration → leave `sd` at its input value where it can't be measured,
          rather than fitting a `log(sd) ~ log(adp)` replacement
    - [x] Thin-sample smoothing dropped entirely
- [ ] Nothing else. Do not start this without the triggering problem.

**Limitations accepted with this decision** (full version in § 5.5), recorded so they read as
choices rather than oversights if this is ever revisited:

- Only 2020–2024 archived; 2010–2019 available today but not pulled, possibly gone later
- No platform ADP archived, so any future model has train/serve skew of unmeasurable size
- ADP volatility — the strongest durable predictor in `STDEV_MODEL_LONGTERM.md` — is
  unavailable, not merely difficult

`STDEV_MODEL_LONGTERM.md` is retained as a **method reference for a day that may never come**,
not as a plan.

---

## Cross-cutting — start now, pays off later

- [ ] Log every availability prediction:
      `timestamp, player_id, target_pick, predicted_prob, model_version`
- [ ] Log real draft outcomes: `draft_id, player_id, actual_pick, league_settings`

Produces nothing today, which is exactly why it's easy to skip. It is the only path to ever
scoring this model against reality — bucket predictions by decile and check whether the
70–80% bucket actually happened ~75% of the time.

---

## Tuning, once the sim runs

Not blockers. Revisit with output in hand (§ 12).

- [ ] Sweep `PLATFORM_WEIGHT` 0 → 1. If availability barely moves, cut the shift mechanism
- [ ] Compare simulated positional-run frequency to real drafts; tune `STARTER_DEADLINE` /
      `NEED_BONUS`
- [ ] Revisit `RHO` once real draft logs exist — it cannot be fit from ADP/stdev alone

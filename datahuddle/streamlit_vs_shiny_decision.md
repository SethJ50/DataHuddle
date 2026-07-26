# Streamlit vs. Shiny — Framework Decision

## Feature Overview

This isn't a new feature — it's an infrastructure decision about which UI framework
DataHuddle should be built on going forward. The question came up because of three
concerns raised while evaluating a Streamlit proof-of-concept against the current
PyShiny app:

1. **Interactive tables** — a preference for how Streamlit's tables look and feel.
2. **The rerun model** — Streamlit reruns the entire script top-to-bottom on every
   widget interaction, which raises a fair worry about wasted recomputation as the
   app grows.
3. **Headshot image loading** — images are loading slowly in the Streamlit POC, which
   reads as a mark against Streamlit specifically.

The rest of this document grounds each of these in what's actually in the codebase
today — including one confirmed bug, one already-working proof-of-concept, and one
roadmap item that matters more than it might seem — rather than answering from
generic framework reputation. It ends with a specific recommendation.

## Current State (grounding facts)

A few facts about the app as it stands today shape everything below:

- **Only 2 of 8 planned panels are built.** `panels/player_profile.py` (63 lines) and
  `panels/adp_comparison.py` (115 lines) are real; the other six
  (`team_profile.py`, `team_depth_charts.py`, `draft_plans.py`, `fpts_vs_xfp.py`,
  `pace_of_play.py`, `defensive_performance.py`) are 11-line "coming soon" stubs.
  Whatever gets decided here, the app is at the cheapest possible point to decide it
  — there isn't much built code at stake yet.

- **Shiny's table grid hit a real, confirmed image-rendering bug.** The docstring in
  `presentation/adp_comparison_view.py` explains that an earlier attempt to embed
  `shiny.ui.img()` tags inside a `shiny.render.DataGrid` column caused a client-side
  (browser) serialization error requiring an active Shiny session. That's not a style
  opinion — it's a limitation that was tried and hit. It's why the shipped
  `AdpComparisonView.shape()` has no headshot column at all for the Shiny page, and
  why its `include_headshot` argument only exists for non-Shiny callers.

- **The Streamlit POC is a real, working answer to that specific bug** —
  `streamlit_poc/adp_comparison_app.py` renders headshots inline via
  `st.dataframe(..., column_config={"headshot_url": st.column_config.ImageColumn(...)})`,
  and it works. More importantly, it reuses `AppContext`, `AdpComparisonService`, and
  `AdpComparisonView` completely unchanged from the Shiny app. That's a live
  demonstration that this app's layered architecture
  (`repositories/` → `adapters/` → `services/` → `presentation/`) doesn't care which
  UI framework sits on top of it — only the `panels/` layer does.

- **The POC already solved the hard part of the headshot problem.** Its
  `_headshot_data_uri()` helper shrinks each NFL.com headshot (~4.2MB once
  base64-encoded at full resolution) down to a ~1-2KB JPEG thumbnail, and caches the
  result per URL with `@st.cache_data`. What's left is a **cold-start cost**: with
  ~296 players in the comparison table, those thumbnails are fetched one at a time,
  in a plain `.apply()` loop with no concurrency. That's a real, fixable performance
  bug — but it's a "fetch things one at a time over the network" bug, not a
  Streamlit-vs-Shiny bug. The exact same loop would be slow if it were called from
  Shiny instead. It's also currently an *in-memory-per-process* cache, so the cost is
  paid again every time the server restarts or redeploys, regardless of framework.

- **Shiny's reactive graph is already doing real work in this app.** Both built
  panels use a deliberate two-tier `@reactive.calc` pattern: an expensive calculation
  is isolated behind a `@reactive.calc` that only reruns when the input it actually
  depends on changes, and a cheaper calculation is layered on top for filtering.
  `adp_comparison.py` takes this furthest, with three layered calcs — one loads and
  joins three ADP platforms (expensive, keyed only on scoring format), one filters by
  position (cheap), and one filters by search text (cheap) — explicitly so that
  typing in the search box never re-triggers the expensive three-platform join.
  Shiny does this automatically, because its reactive graph only reruns the nodes
  downstream of whatever input actually changed.

- **The Streamlit POC mirrors that pattern, but by hand.** It uses
  `@st.cache_resource` for the one-time `AppContext` build and `@st.cache_data` keyed
  on scoring format for the expensive join — the same two-tier idea, achieving the
  same result. The difference isn't whether it's possible in Streamlit (it clearly
  is, and it works) — it's that Shiny gets this for free from its dependency graph,
  while Streamlit requires a developer to notice which computations are expensive and
  cache them correctly, one function at a time, on every page.

- **The most demanding page on the roadmap hasn't been built yet.** `PLANNING.md`
  describes an upcoming **Draft Plan** page: an editable, multi-row draft board with
  per-row markings and a computed Diff column. That's the one page shape — many
  small, interdependent, frequently-edited widgets on one screen — where a
  full-script rerun needs the most manual caching discipline to stay fast. It's worth
  calling out by name because it's a materially harder case than anything built so
  far.

- **Neither dependency is currently pinned anywhere.** There's no
  `requirements.txt` or `pyproject.toml` in the repo. The versions currently
  installed are `streamlit==1.60.0` and `shiny==1.5.0`. Whichever framework(s)
  survive this decision, this gap is worth closing.

## The Case for Streamlit

- **Table and cell rendering is genuinely richer.** `st.column_config` supports
  `ImageColumn`, `LinkColumn`, `ProgressColumn`, and more, directly on a plain
  dataframe — no workaround needed. This is the exact feature Shiny's `DataGrid`
  couldn't deliver without hitting the serialization bug above.
- **`st.data_editor` gives an editable-table story for free**, which matters
  specifically for the upcoming Draft Plan board (add a pick, edit a marking, see
  Diff recompute) — Streamlit has a purpose-built widget for exactly that shape of
  UI.
- **Lower iteration friction for a data-and-viz-heavy solo app.** Streamlit's
  script-runs-top-to-bottom model is simple to reason about for straightforward
  filter-and-display pages, which describes most of what's built and most of what's
  planned.
- **The POC already proves zero rework below the UI layer.** Every repository,
  adapter, service, and presentation class the app already has would carry over
  untouched.

## The Case for Shiny

- **The reactive dependency graph is correctness-by-construction.** Nothing has to be
  manually cache-keyed for Shiny to avoid rerunning an expensive step unnecessarily —
  it only reruns what actually depends on the input that changed. As more pages get
  built with more interacting widgets, this advantage compounds; Streamlit's version
  of the same guarantee has to be re-earned by hand on every new page.
- **The pattern that provides this is already proven in this codebase**, not just in
  theory — both built panels use it today, and `adp_comparison.py`'s three-layer calc
  chain is a direct, working example of exactly the problem the user is worried
  about, already solved.
- **The module-per-panel structure already fits the remaining roadmap.** Six more
  panels are stubbed out in exactly the shape (`@module.ui`/`@module.server` pairs
  wired into `app.py`) that the next features need — no structural rework required
  to keep building in Shiny.
- **Nothing that already works has to be rewritten.** `player_profile.py` and
  `adp_comparison.py` are done, tested by use, and live.

## The Three Concerns, Addressed Directly

**Interactive tables / look.** This is a legitimate, confirmed Shiny gap — not a
matter of taste. `DataGrid` could not render an image column without breaking; it
was tried and abandoned specifically because of that. If interactive tables with
inline images matter to how this app gets used day to day, that preference is backed
by a real limitation, not just aesthetics.

**Rerun-on-every-change.** This concern is valid in general, but the codebase itself
shows the mitigation is straightforward for the app's current shape: the exact
caching pattern Streamlit needs (an expensive, narrowly-keyed cache plus cheap
filtering on top) is already implemented twice in Shiny and once in the Streamlit
POC. The real risk isn't the app as it exists today — it's concentrated almost
entirely in the not-yet-built Draft Plan page, which is dense with small
interdependent widgets. That's a reason to test that one page carefully, not a
reason to rule out Streamlit for the whole app.

**Headshot load speed.** This is the one concern that's arguably been misdiagnosed.
The slow part isn't Streamlit's table rendering or its rerun model — the POC's
thumbnail-and-cache approach already solves the actual hard problem (an image too
large to embed 296 times over). What's left is a plain performance bug: images are
fetched one at a time with no concurrency, and the cache doesn't survive a process
restart. Both of those are fixable in either framework, and neither is a reason to
prefer Shiny over Streamlit specifically.

## Migration Cost / Reversibility

What would actually move in a migration: `panels/player_profile.py`,
`panels/adp_comparison.py`, and their wiring in `app.py` — roughly 180 lines of
Shiny-specific UI code, plus six trivial 11-line stubs that haven't been built out
yet either way.

What would not move at all: `repositories/`, `adapters/`, `services/`,
`presentation/`, `db/`, `registry.py`, and `scoring.py` — every one of these is
already proven framework-agnostic, because the Streamlit POC uses them unchanged
today. This is the strongest practical argument for why this decision is lower-stakes
than it might feel: the app's actual logic and data layer isn't part of the bet
either way.

## Recommendation

**Migrate to Streamlit**, with two explicit conditions attached rather than an
unconditional switch:

1. **Fix the headshot cold-start cost as its own task, regardless of this decision.**
   Fetch thumbnails concurrently instead of one at a time, and persist the resized
   thumbnails somewhere that survives a process restart (a Mongo collection or an
   on-disk cache directory) instead of relying on `@st.cache_data`'s in-memory,
   per-process cache alone.
2. **Time-box a real prototype of the Draft Plan board using `st.data_editor` before
   migrating everything else.** It's the one page shape on the roadmap where
   Shiny's automatic dependency graph would have given something for free that
   Streamlit's rerun model doesn't — worth de-risking on its own before committing
   the rest of the app to the switch.

The reasoning: switching cost is genuinely low right now — two real panels, about
180 lines of framework-specific code, and an architecture the POC has already proven
carries over cleanly. The table/image preference isn't a soft opinion; it's backed
by a bug Shiny actually hit. And the rerun-model risk, while real, is narrow and
identifiable rather than spread evenly across the app — which is exactly the kind of
risk a short, targeted prototype can retire before committing further.

## Next Steps

1. Fix headshot caching — concurrent fetch, persistent (not purely in-memory) store
   — independent of the framework decision.
2. Build a small `st.data_editor` prototype of the Draft Plan board's core
   interaction (add/edit a row, recompute Diff) and confirm it feels responsive
   before migrating anything else.
3. If that prototype feels good: port `player_profile.py` and `adp_comparison.py` to
   Streamlit for real (not the current throwaway POC script), replace
   `ui.page_navbar`'s `nav_menu` grouping with Streamlit's multipage/`st.navigation`
   structure, and retire `streamlit_poc/`.
4. Add a `requirements.txt` or `pyproject.toml` pinning whichever framework(s)
   remain — neither is currently pinned anywhere in the repo.

# Streamlit Migration — Multi-Page Shell + Player Profile + Draft Plan

## Feature Overview

`datahuddle/streamlit_vs_shiny_decision.md` already recommends migrating
DataHuddle from PyShiny to Streamlit, conditioned on prototyping the Draft
Plan board first — which has now happened (`streamlit_poc/draft_plan_app.py`,
which uses the position-first `st.tabs()` layout from
`datahuddle/draft_plan_ui_position_tabs.md`). Having seen both POCs
(`streamlit_poc/adp_comparison_app.py` and `draft_plan_app.py`) work, this
feature starts the real migration: a permanent Streamlit multi-page app (not
throwaway scripts), beginning with the two panels that already have working
logic behind them — **Player Profile** and **Draft Plan**.

Today, `app.py` (Shiny, 80 lines) is the only real entrypoint: one
`ui.page_navbar` with three `nav_menu` groups ("General", "Pre-Draft", "Daily
Fantasy") wrapping 8 `panels/*.py` modules, of which only `player_profile.py`
and `adp_comparison.py` are built — the other 6 (including `draft_plans.py`)
are 11-line "coming soon" stubs. Streamlit 1.60.0 is already installed and
fully supports the programmatic `st.navigation`/`st.Page` multipage API (no
`pages/`-folder auto-discovery magic needed) — confirmed via `pip show
streamlit` and a repo-wide search that found no `.streamlit/config.toml`,
`pages/` folder, or existing `st.navigation` usage anywhere.

**Decisions made:**
- This migration starts with **only** Player Profile + Draft Plan — the
  other 6 panels stay Shiny-only for now, added to Streamlit later one at a
  time.
- Navigation uses Streamlit's **default left sidebar** (grouped by section,
  mirroring the "General"/"Pre-Draft" `nav_menu` labels), not a top nav bar.
- `streamlit_poc/draft_plan_app.py` gets **deleted** once its replacement is
  verified working.
- The existing Shiny `app.py` is left completely unmodified and keeps
  running in parallel — this feature is purely additive.

**Update:** ADP Comparison has since been added to this same migration
(Implementation Guide section 7 below), since its POC
(`streamlit_poc/adp_comparison_app.py`) was already working and the pattern
is a near-identical port to Draft Plan's. It joins the "Pre-Draft" sidebar
group alongside Draft Plan, matching where Shiny's own nav puts "ADP
Platform Comparison" (`app.py:32-46`).

**Target structure:**

```
DataHuddle/
├── streamlit_app.py            <- NEW entrypoint (repo root, sits next to app.py)
├── streamlit_state.py          <- NEW shared cached AppContext helper
├── pages/
│   ├── home.py                 <- NEW minimal landing page
│   ├── player_profile.py       <- NEW real port of panels/player_profile.py
│   ├── draft_plan.py           <- NEW real port of streamlit_poc/draft_plan_app.py
│   └── adp_comparison.py       <- NEW real port of streamlit_poc/adp_comparison_app.py
└── streamlit_poc/              <- DELETED entirely once both pages above are verified
    ├── adp_comparison_app.py
    └── draft_plan_app.py
```

Placing `streamlit_app.py` at the repo root (rather than nested in
`streamlit_poc/`) means page scripts under `pages/` can import
`app_context`, `scoring`, `registry`, etc. directly — Streamlit adds the
entrypoint's own directory to `sys.path`, so **the `sys.path.insert(...)`
hack both POC scripts needed goes away entirely** for the new pages.

## TODO List

1. Create `streamlit_state.py` — one shared `@st.cache_resource`-wrapped
   `get_app_context()` for every page to import.
2. Create `streamlit_app.py` — the entrypoint: `st.set_page_config()` once,
   then `st.navigation()` wiring the Home/Player Profile/Draft Plan pages
   into "General"/"Pre-Draft" sidebar sections.
3. Create `pages/home.py` — minimal landing page, mirrors Shiny's empty
   Home tab.
4. Create `pages/player_profile.py` — port of `panels/player_profile.py`,
   reusing `ctx.roster_service`, `ctx.player_directory`, and
   `GameLogView.shape()` unchanged.
5. Create `pages/draft_plan.py` — port of `streamlit_poc/draft_plan_app.py`,
   dropping the per-file `sys.path` hack and local `get_app_context()`/
   `st.set_page_config()` in favor of the shared `streamlit_state.py`.
6. Create `pages/adp_comparison.py` — port of
   `streamlit_poc/adp_comparison_app.py`, same treatment as Draft Plan
   (shared `get_app_context()`, no `sys.path` hack), added to `streamlit_app.py`'s
   "Pre-Draft" sidebar group alongside Draft Plan.
7. Manually verify all three pages end-to-end (see Verification Checklist
   below), then delete `streamlit_poc/draft_plan_app.py` and
   `streamlit_poc/adp_comparison_app.py`.

## Implementation Guide

### 1. `streamlit_state.py` — shared cached AppContext

One `@st.cache_resource`-wrapped `get_app_context()`, identical in spirit to
what both POCs already do (`streamlit_poc/draft_plan_app.py:27-41`), but
defined once so `pages/player_profile.py` and `pages/draft_plan.py` both
import it instead of each defining their own copy:

```python
import streamlit as st
from app_context import AppContext

SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]

@st.cache_resource(show_spinner="Loading player data (nflreadpy + MongoDB) -- this can take a minute...")
def get_app_context() -> AppContext:
    """
    Purpose: Builds AppContext exactly once per Streamlit server process,
        no matter how many pages get visited or how many times any single
        page reruns from a widget interaction.
    Returns: AppContext, the same composition-root object app.py (the Shiny
        entrypoint) builds at module scope.
    Notes:
        @st.cache_resource caches by process, not by page -- every page
        that calls this function shares the same singleton AppContext
        instead of each rebuilding the whole repositories/adapters/services
        graph from scratch.
    """
    return AppContext(SEASONS)
```

**Context check:** `@st.cache_resource` is Streamlit's decorator for
sharing a live, un-copied object (like a database connection or, here, the
whole `AppContext` object graph) across every rerun and every user session
in one server process — the opposite of `@st.cache_data`, which is for
plain, copyable data values like DataFrames.

### 2. `streamlit_app.py` — entrypoint + navigation

`st.set_page_config` may only be called once per run and must be the first
Streamlit call — it belongs here, not in individual page files (unlike the
POCs, which each called it themselves since they ran standalone).

```python
import streamlit as st

st.set_page_config(page_title="DataHuddle", layout="wide")

home_page = st.Page("pages/home.py", title="Home")
player_profile_page = st.Page("pages/player_profile.py", title="Player Profile")
draft_plan_page = st.Page("pages/draft_plan.py", title="Draft Plan")
adp_comparison_page = st.Page("pages/adp_comparison.py", title="ADP Platform Comparison")

# The dict keys below become sidebar section headers -- the direct
# Streamlit analog of Shiny's ui.nav_menu("General", ...) /
# ui.nav_menu("Pre-Draft", ...) groups in app.py:21-46.
pg = st.navigation({
    "": [home_page],
    "General": [player_profile_page],
    "Pre-Draft": [adp_comparison_page, draft_plan_page],
})
pg.run()
```

Run with `streamlit run streamlit_app.py`.

**Context check:** `st.Page(path, title=...)` wraps a script file as one
navigable page; `st.navigation(pages_dict)` builds the sidebar from those
pages (dict keys group them under section headers) and returns a `Page`
object — calling `.run()` on it actually executes whichever page is
currently selected. This is Streamlit's newer (v1.36+) programmatic
multipage API, not the older convention of an auto-discovered `pages/`
folder with no code wiring it together.

### 3. `pages/home.py` — minimal landing page

Mirrors `app.py:18-20`'s empty `ui.nav_panel("Home")`:

```python
import streamlit as st

st.title("DataHuddle")
st.write("Select a page from the sidebar to get started.")
```

### 4. `pages/player_profile.py` — port of `panels/player_profile.py`

Reuses `ctx.roster_service.player_names()`, `ctx.player_directory` (all four
lookup methods), and `GameLogView.shape()` (`presentation/gamelog_view.py`)
completely unchanged — only the UI layer is being rewritten.

```python
import streamlit as st

from streamlit_state import get_app_context
from presentation.gamelog_view import GameLogView

ctx = get_app_context()

DEFAULT_CANONICAL_ID = "00-0037239"  # Chris Olave, same default as the Shiny page

# ctx.roster_service.player_names() returns {canonical_id: display_name}.
# Streamlit's selectbox needs a flat list of options to choose from, so we
# build the reverse lookup (display_name -> canonical_id) to go from
# "whichever name the user picked" back to the id every other lookup needs.
names_by_id = ctx.roster_service.player_names()
id_by_name = {name: cid for cid, name in names_by_id.items()}
sorted_names = sorted(id_by_name)

default_name = names_by_id.get(DEFAULT_CANONICAL_ID)
default_index = sorted_names.index(default_name) if default_name in sorted_names else 0

left, right = st.columns([3, 9])  # same 3:9 ratio as ui.layout_columns' col_widths

with left:
    selected_name = st.selectbox("Player", sorted_names, index=default_index)
    canonical_id = id_by_name[selected_name]

    headshot_url = ctx.player_directory.get_headshot(canonical_id)
    # st.image accepts both a full URL (nflreadpy's CDN) and a local relative
    # path -- PlayerDirectory.get_headshot() falls back to the local
    # "www/defaultPlayer.png" file when a player has no headshot on record,
    # and st.image handles both cases the same way, so no onerror JS
    # workaround (like the Shiny version needed) is necessary here.
    st.image(headshot_url, width=200)

with right:
    st.subheader("Box Scores")
    gamelog_df = GameLogView.shape(
        ctx.player_directory.get_gamelog(canonical_id),
        ctx.player_directory.get_position(canonical_id),
    )
    st.dataframe(gamelog_df, hide_index=True, use_container_width=True)
```

**Context check:** unlike the Shiny version's `@reactive.calc`, there's no
manual caching needed here — `get_gamelog()`/`GameLogView.shape()` are cheap
pandas filtering, and Streamlit reruns this whole page script fresh each
time the dropdown changes anyway, same as the POCs already do for their own
per-widget logic.

### 5. `pages/draft_plan.py` — port of `streamlit_poc/draft_plan_app.py`

Move the file's contents almost as-is (it already implements the approved
Option A position-tabs layout, `datahuddle/draft_plan_ui_position_tabs.md`),
with two changes: drop the local `sys.path` hack and local
`get_app_context()`/`st.set_page_config()` (now shared/centralized), and
import `get_app_context` from `streamlit_state` instead:

```python
import pandas as pd
import streamlit as st

from streamlit_state import get_app_context
from scoring import ScoringFormat

ctx = get_app_context()

MARKING_OPTIONS = ["Safe", "Upside", "Late", "Early"]
POSITIONS = ["QB", "RB", "WR", "TE"]
BOARD_COLUMNS = ["Pick", "Player", "ADP", "True Value Rank", "Diff", "Marking", "Locked In"]

# ... rest is identical to streamlit_poc/draft_plan_app.py's existing
# Setting-Inputs container, pick_labels()/rank_candidates() calls, and the
# st.tabs(POSITIONS) loop with per-position st.data_editor + st.session_state.
```

Everything downstream of the imports (the input row, `pick_options`,
`by_name_by_position`, the `st.tabs` loop, the `data_editor` column_config,
and the ADP/True Value Rank/Diff recompute) copies over unchanged from
`streamlit_poc/draft_plan_app.py:44-102` — `DraftPlanService.pick_labels()`
and `.rank_candidates()` (`services/draft_plan_service.py`) need no changes.

### 6. `pages/adp_comparison.py` — port of `streamlit_poc/adp_comparison_app.py`

Same treatment as Draft Plan: reuse `ctx.adp_comparison_service`,
`AdpComparisonView`, `ScoringFormat`, and `Position` completely unchanged,
drop the per-file `sys.path` hack, and pull `get_app_context` from the
shared `streamlit_state.py` instead of defining it locally.

```python
import streamlit as st

from streamlit_state import get_app_context
from scoring import ScoringFormat
from registry import Position
from presentation.adp_comparison_view import AdpComparisonView

ctx = get_app_context()


@st.cache_data(show_spinner="Comparing ADP across ESPN, Yahoo, and Sleeper...")
def get_comparison_data(fmt: ScoringFormat):
    """
    Purpose: Runs the expensive step -- loading and resolving all three ADP
        platforms -- only when the scoring-format choice actually changes.
    Parameters:
        fmt (ScoringFormat): HALF_PPR or FULL_PPR.
    Returns:
        pd.DataFrame, ctx.adp_comparison_service.compare(fmt)'s output.
    Notes:
        @st.cache_data keys its cache on this function's arguments (here,
        just fmt) -- switching the scoring-format dropdown computes a fresh
        entry, but changing the position filter or search text (which don't
        affect this function's inputs) reuses the cached result instead of
        redoing the 3-platform join. Mirrors panels/adp_comparison.py's
        comparison_data()/display_data() two-tier @reactive.calc split.
    """
    return ctx.adp_comparison_service.compare(fmt)


st.title("ADP Platform Comparison")

# Same three controls as panels/adp_comparison.py: position filter,
# scoring-format toggle, name search -- laid out in one row.
col1, col2, col3 = st.columns(3)

with col1:
    position_choices = ["All"] + [p.value for p in Position]
    position = st.selectbox("Position", position_choices, index=0)

with col2:
    format_labels = {"Half PPR": ScoringFormat.HALF_PPR, "Full PPR": ScoringFormat.FULL_PPR}
    format_label = st.selectbox("Scoring Format", list(format_labels.keys()), index=0)
    scoring_format = format_labels[format_label]

with col3:
    search = st.text_input("Search Player", placeholder="Search by name...")

# Expensive compare() (cached) -> cheap position filter/reshape via
# AdpComparisonView.shape() -> cheap manual pandas search filter. Same
# three-tier shape as the Shiny page's reactive.calc chain, just without
# needing @reactive.calc to get it -- @st.cache_data covers the one
# expensive step, and everything after it is cheap enough to just rerun.
comparison_df = get_comparison_data(scoring_format)
display_df = AdpComparisonView.shape(comparison_df, position)

query = search.strip()
if query:
    display_df = display_df[display_df["Player"].str.contains(query, case=False, na=False)]

st.dataframe(display_df, hide_index=True, use_container_width=True)
```

**Context check:** `AdpComparisonView.shape()`
(`presentation/adp_comparison_view.py`) intentionally has no headshot
column — its own docstring documents that headshots were tried here twice
(Shiny's `ui.img()` in `render.DataGrid`, which hit a client-side
serialization error; then Streamlit's `st.column_config.ImageColumn`, which
worked but wasn't kept) and removed both times. Nothing in this port
reintroduces one; if headshots come back on this page later, `ImageColumn`
is the proven-working option, not `ui.img()`.

### 7. Clean up the POCs

Once `pages/draft_plan.py` and `pages/adp_comparison.py` are both confirmed
working end-to-end (see Verification Checklist below), delete
`streamlit_poc/draft_plan_app.py` and `streamlit_poc/adp_comparison_app.py`
— at that point every script in `streamlit_poc/` has a real replacement, so
the whole folder can go.

## Verification Checklist

- [ ] `streamlit run streamlit_app.py` — sidebar shows "Home" (ungrouped),
      "General → Player Profile", "Pre-Draft → ADP Platform Comparison,
      Draft Plan", and the app loads without import errors. (This validates
      the no-`sys.path`-hack assumption in the Target Structure section
      above — if any page fails to import `app_context`/`scoring`/etc., add
      back a `sys.path` insert in `streamlit_state.py` as a fallback.)
- [ ] **Player Profile**: Chris Olave is preselected with a visible
      headshot and box-score table; switch to a couple of other players
      (including one at each of QB/RB/WR/TE) and confirm the headshot and
      the game log's position-specific columns (passing vs. rushing vs.
      receiving, per `GameLogView.shape()`) update correctly; confirm a
      player with no headshot on record shows the default image instead of
      a broken image.
- [ ] **Draft Plan**: same checklist as `DRAFT_PLAN_PAGE.md`'s existing one
      — add/remove candidate rows in each position tab; switching Platform
      and Scoring Format recomputes ADP/True Value Rank/Diff without losing
      rows; pick labels are correct for a couple of different (teams, draft
      position) combinations.
- [ ] **ADP Comparison**: switching Position filters the table without a
      visible reload delay (confirms `@st.cache_data` is actually keyed on
      scoring format only, not position); switching Scoring Format changes
      the ADP numbers shown (ESPN/Yahoo/Sleeper each track separate
      half/full-PPR ADP); typing in the search box filters by name;
      clearing the search box restores the full (position-filtered) table.
- [ ] Confirm the existing Shiny app (`shiny run --reload --launch-browser
      app.py`) still runs unaffected — nothing in this feature touches
      `app.py` or any `panels/*.py` file.

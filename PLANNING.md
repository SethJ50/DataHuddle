# DataHuddle Product Planning Document

## Context

[readme.md](/Users/sethjernigan/Desktop/DataHuddle/readme.md) describes a large surface area (a DFS section and a Draft Analysis section, each with multiple pages, filters, widgets, and player-marking systems) but leaves the underlying mechanics — data sources, persistence model, player identity across sources, marking semantics — unspecified. This document resolves those ambiguities (via a clarifying Q&A with the user) and lays out a concrete, page-by-page reference to write code against going forward. This is a planning/reference document, not a code-change plan — the deliverable of this task is this document itself, saved into the repo (e.g. `PLANNING.md`) for ongoing reference.

**Decisions made:**
- **Build priority: Draft Analysis section first**, DFS section after.
- **Single-user app, no auth/login.**
- **Markings & notes are separate per context** (DFS markings independent from Draft Analysis markings — different vocabularies, different judgments).
- **Player identity across sources resolved via a manually curated mapping table**, not fuzzy matching.
- **Team Depth Chart ordering** = rank by our own `FfbData` projections, not an external depth chart feed.
- **ADP platforms to track: ESPN, Yahoo, Sleeper.**
- **Draft Plans are named, saved documents in Mongo** (not session-only).
- **Player Categories (e.g. "Uncertain Backfields") are manually curated tags for now**, not rule-computed.
- **Draft Plan "True Value"** = rank from our own `FfbData` projections; **Diff** = ADP rank − True Value rank.
- **Play-by-play-derived metrics** (Player Shares, Pace of Play, EPA comparisons) — data source **deferred**, to be decided when those pages are actually built.
- **Home page** = simple landing/welcome page, low priority.

## Existing Architecture (recap, already built)

- **PyShiny app**, module-per-nav-panel pattern: `panels/<name>.py` with `@module.ui`/`@module.server` pairs, assembled in [app.py](/Users/sethjernigan/Desktop/DataHuddle/app.py). Adding a page = new `panels/` file + 2 lines in `app.py`. Reuse this pattern for every new page below.
- **[data_manager.py](/Users/sethjernigan/Desktop/DataHuddle/data_manager.py)**:
  - `NflDataRepo` — wraps `nflreadpy.load_player_stats`, loaded once at app startup (live data, not Mongo-backed, per earlier decision).
  - `FfbData` — reads `ffb_qb_projections`/`ffb_flex_projections` collections from Mongo via `db/reader.py`; `get_player_projections()` combines them into season-long + per-game fantasy point projections (regular/half-PPR/full-PPR).
  - `UIData` — shapes game log data for display, position-aware.
- **`db/` package** — generic Mongo access layer: `connection.py` (client/db handle from `MONGODB_URI` env var, db name hardcoded to `"data-huddle"`), `loader.py` (`reload_collection`/`reload_collection_from_csv` — clear + bulk insert), `reader.py` (`read_collection` — pull a collection into a DataFrame).
- **[scripts/load_data.py](/Users/sethjernigan/Desktop/DataHuddle/scripts/load_data.py)** — generic CLI, no args: globs `data/*.csv`, loads each into a collection named after its filename stem. This is the reusable ingestion path for any new raw data source (ADP files, DFS salary files, etc.) — just drop a correctly-named CSV in `data/` and rerun it.
- **[user_interface.py](/Users/sethjernigan/Desktop/DataHuddle/user_interface.py)** — `UI.make_table()` styles a DataFrame as a Pandas Styler for Shiny rendering.

## New Cross-Cutting Data Model (MongoDB collections)

These are shared foundations multiple pages depend on — build before or alongside the first page that needs them.

- **`player_id_map`** — resolves name spelling differences across sources.
  ```
  { canonical_id, source: "espn" | "yahoo" | "sleeper" | "nflreadpy", source_name }
  ```
  `canonical_id` should key off `nflreadpy`'s `player_display_name` (already the backbone of `NflDataRepo`) wherever possible, since that's the source of truth for headshots/game logs. New collections that reference players (ADP tables, notes, draft plans) store `canonical_id`, not raw source names.

- **`player_notes`** — markings + free-text notes, separate per context.
  ```
  { canonical_id, context: "draft" | "dfs", marking, note, updated_at }
  ```
  Draft context markings: Love, Like, Value, Sleeper, Hate, plus category tags (see `player_categories`). DFS context markings: Love, Like, Value, Cash, GPP.

- **`player_categories`** — manually curated category tags (e.g. "Upside Mid-Round Receiver", "Uncertain Backfield").
  ```
  { canonical_id, category, note }
  ```
  Kept separate from `player_notes` since a player can sit in multiple categories at once, unlike a single marking.

- **`adp_espn`, `adp_yahoo`, `adp_sleeper`** — raw ADP uploads, one collection per platform, loaded via the existing generic `scripts/load_data.py` (filename stem = collection name, e.g. `data/adp_espn.csv` → `adp_espn`).

- **`draft_plans`** — named, saved draft configurations.
  ```
  {
    name, num_teams, draft_position, platform,
    picks: [{ round, pick, position, canonical_id, marking }]
  }
  ```
  Multiple documents = multiple saved drafts, listed on a landing view of the Draft Plan page; user can create new ones or duplicate an existing one.

## Player Identity Resolution

New module (suggest `player_identity.py` at project root, alongside `data_manager.py`): a function that takes a source name + source label and returns the canonical id, looking it up in `player_id_map`. When loading ADP/DFS files, any name that fails to resolve should be surfaced clearly (e.g. printed by the load script) so it can be added to `player_id_map` manually — this is the "manual mapping table" workflow the user chose over fuzzy matching.

## Page-by-Page Reference

### Draft Analysis (build first)

**Player Profile** — closely mirrors the existing `panels/player_profile.py`; extend rather than rewrite:
- Reuse: player dropdown, headshot (`NflDataRepo.get_player_headshot`), game log (`UIData.make_player_gamelog_data`).
- Add: projections panel from `FfbData.get_player_projections()` filtered to the selected player.
- Add: markings UI (Love/Like/Value/Sleeper/Hate + category tags) and a notes text field, read/write against `player_notes` with `context="draft"`.

**Team Profiles**
- Filters: Team, Year.
- Depth Chart widget: for the selected team, rank players per position using `FfbData.get_player_projections()` fantasy point columns → QB1/QB2, RB1-3, WR1-5, TE1-2. Columns: Fantasy Projection, ADP (once ADP data is loaded), marking buttons (Love/Like/Value/Sleeper/Hate), editable team notes.
- Player Shares widget (Target/Rush/Goal Line/Red Zone/Deep Target share): **data source deferred** — likely `nfl.load_pbp()` play-by-play, but not committed; flag clearly in code as a TODO until decided.
- Game log widget — reuse existing box-score pattern.

**ADP Platform Comparison**
- Data: `adp_espn`, `adp_yahoo`, `adp_sleeper` collections, joined via `player_id_map`.
- UI: two platform-select dropdowns, resulting table searchable/sortable/filterable by position (reuse `UI.make_table()` styling where applicable, though this table likely needs interactive sort/filter beyond the static Styler — may need `render.data_frame`/`DataGrid` instead of `render.table`).

**Player Categories / Markings**
- Simple table view per category, querying `player_categories` for players tagged into "New Top 12 Receiver Candidate" / "Uncertain Backfields" / etc. Purely a read view over manually-curated tags — no computation.

**Draft Plan**
- Landing view lists saved `draft_plans` documents (by `name`), with create-new / duplicate actions.
- Per-plan editor: inputs for `num_teams`, `draft_position`, `platform`; then per core position (QB/RB/WR/TE) an editable table with columns Round, Pick, `{pos}` Name (searchable player dropdown), ADP, True Value (rank from `FfbData` projections), Diff (ADP rank − True Value rank, computed not stored), markings (Safe/Upside/Late/Early).
- "Available %" column explicitly deferred per readme ("a future model") — leave as a placeholder column, not implemented now.

### DFS (build after Draft Analysis)

**Player Profile** — dropdown, headshot, info-bug (position/team), DFS salary bug (**salary data source not yet specified** — flag as open item), game log, markings (Love/Like/Value/Cash/GPP, `context="dfs"`) + notes.

**Team Profile** — DFS-tailored team view; scope TBD until reached.

**Actual FPTS vs. XFP / Pace of Play / FPTS-per-Rush vs EPA-per-Rush / FPTS-per-Pass-Att vs EPA-per-Pass-Att** — all plot pages requiring play-by-play/EPA data; same deferred-data-source question as Player Shares above. Don't start these until that data source decision is made.

### Home
Simple landing/welcome page: app title, short description, links to main pages. Low priority — build last or whenever convenient.

## Recommended Build Order

1. Foundational: `player_id_map` + `player_identity.py` resolution helper, `player_notes`/`player_categories` collections and a reusable markings-UI component (buttons + notes field) other panels can embed.
2. Draft Analysis Player Profile (biggest overlap with what already exists).
3. Load ESPN/Yahoo/Sleeper ADP CSVs via the existing generic loader; build ADP Platform Comparison.
4. Player Categories / Markings page.
5. Team Profiles (Depth Chart via projections ranking now; Player Shares widget deferred until pbp data source is decided).
6. Draft Plan.
7. DFS section pages, once play-by-play/EPA and DFS-salary data sourcing questions are resolved.

## Open Items (explicitly deferred, not blocking)
- Data source for Player Shares (Target/Rush/Goal Line/Red Zone/Deep Target share) — likely `nfl.load_pbp()`, unconfirmed.
- Data source/platform for DFS salaries.
- Exact CSV column formats for `adp_espn`/`adp_yahoo`/`adp_sleeper` — define when actually uploading that data.
- "Available %" model for Draft Plan — future work per readme, not in scope now.

## Verification
This task's output is the planning document itself — no code changes to verify. Once approved, save this content as `PLANNING.md` (or similar) in the repo root so it's available for reference during future implementation sessions.

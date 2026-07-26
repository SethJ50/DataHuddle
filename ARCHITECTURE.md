# DataHuddle Architecture — Plain-English Overview

## Why this exists

The app used to have one file, `data_manager.py`, that did everything: fetched
data from MongoDB, cleaned up messy spreadsheet columns, calculated fantasy
points, *and* decided which columns to show on screen — all mixed together in
three classes. That made it hard to add new features without accidentally
breaking old ones, and the fantasy-scoring math was copy-pasted in three
different files.

The new design splits those jobs into separate, small pieces that each do
**one thing**. Think of it like an assembly line instead of one person doing
every job — each station only worries about its own step, and you can swap
out or add a station without redesigning the whole line.

## The assembly line, in order

Data flows through five stages, left to right:

```
  Raw sources          Cleanup           Math/Logic         Screen prep         What you see
 ┌───────────┐      ┌────────────┐      ┌───────────┐      ┌─────────────┐    ┌──────────┐
 │Repositories│ ───▶ │  Adapters  │ ───▶ │  Services  │ ───▶ │ Presentation │ ─▶ │  Panels  │
 │ (fetch it) │      │(clean it)  │      │(compute it)│      │ (format it)  │    │ (a page) │
 └───────────┘      └────────────┘      └───────────┘      └─────────────┘    └──────────┘
```

- **Repositories** — go get the raw data (from MongoDB or from the live NFL
  stats service) and remember it so it isn't re-fetched every time.
- **Adapters** — every data source (ESPN, Sleeper, UDK, Yahoo…) names its
  spreadsheet columns differently and has its own quirks. Adapters translate
  each one into one common, tidy shape so nothing downstream has to care
  where the data came from.
- **Services** — the business logic. Right now that's "how many fantasy
  points does this projection translate to," using one shared rulebook.
- **Presentation** — takes a data table and shapes it for display (which
  columns to show, how to style it) — no data-fetching or math happens here.
- **Panels** — the actual pages in the app (`panels/*.py`), each one a tab you
  click on in the navigation bar. Panels only ask the layers above for
  already-prepared data — they don't talk to MongoDB or do math themselves.

Everything is tied together by one object, **`AppContext`**, built once when
the app starts. It holds one instance of every repository/adapter/service and
hands them to whichever panel needs them — like a toolbox that gets passed
around instead of every page building its own tools from scratch.

## What's built today, piece by piece

### The rulebook and address book (shared by everything)

- **`registry.py`** — a plain list of every MongoDB "table" (called a
  *collection*) by name, plus the standard position labels (QB/RB/WR/TE/K/
  DST). Anything that needs a collection name looks it up here instead of
  typing the name out by hand — so a rename only has to happen in one place.
- **`scoring.py`** — the *one* place the fantasy-scoring formula lives
  (yards-per-point, touchdown values, and the three scoring styles: standard,
  half-PPR, full-PPR). Both the app and the data-scraping scripts use this
  same rulebook now, instead of three separate copies that could drift out
  of sync.

### Repositories — "go get the data"

- **`repositories/collection_repo.py` → `CollectionRepo`** — a generic
  fetch-and-remember helper for any MongoDB collection. Ask it to `.read()`
  the first time and it pulls the data; ask again and it hands back the same
  copy from memory instead of hitting the database twice. Call `.refresh()`
  to force a fresh pull.
- **`repositories/nfl_read_repo.py` → `NflReadRepo`** — wraps the live NFL
  stats service (`nflreadpy`), which is *not* in MongoDB. It actually wraps
  *two* separate pulls: game-by-game stats (only for players who've played),
  and a much broader ~25,000-player reference (every player ever rostered,
  including rookies with zero games played yet). Both are the biggest,
  slowest data pulls in the app, so each only happens once and is kept in
  memory.
- **`repositories/player_directory.py` → `PlayerDirectory`** — the "who is
  this player" lookup: given a player's ID, it can tell you their name,
  headshot photo, position, team, or full game-by-game log. Everything is
  looked up by a stable internal ID (not by the player's printed name),
  because two players can share a name, or a name can be spelled slightly
  differently in different places. Name/headshot/position/team lookups use
  the broad player reference (so rookies are covered); game logs use the
  game-by-game stats (since only that source has per-game rows).
- **`repositories/player_identity_repo.py` → `PlayerIdentityRepo`** — the
  "translator" that connects the *same* player across different sources. See
  the dedicated section below — [How player identity resolution works](#how-player-identity-resolution-works-player_id_map) —
  for the full explanation.
- **`db/documents.py`** — a small helper for reading/writing single records
  (as opposed to `CollectionRepo`, which reads a whole table at once). This
  will back the future notes/categories/draft-plan features, which need to
  save one player's note at a time rather than re-loading everything.

### Adapters — "clean up the messy spreadsheet quirks"

- **`adapters/ffb_projections_adapter.py` → `FfbProjectionsAdapter`** —
  cleans up the Fantasy Footballers (UDK) projection data specifically. Their
  export has a spreadsheet quirk (two columns both named "YDS" and "TDS,"
  once for passing and once for rushing) that used to be handled with fragile
  guesswork; now that guesswork lives in exactly one place, clearly labeled.
- **`adapters/udk_rankings_adapter.py` → `UdkRankingsAdapter`** — reads UDK's
  four position-ranking files (QB/RB/WR/TE) and combines them into one list.
  This is the definitive list of "which players does this app care about" —
  see [How the player universe works](#how-the-player-universe-works-rosterservice)
  below.
- **`adapters/adp_source_adapter.py` → `EspnAdpAdapter`, `SleeperAdpAdapter`,
  `YahooAdpAdapter`** — each one exposes one ADP platform's data in the same
  shape (name, team, position, ADP value), so the comparison logic never has
  to special-case which platform it's looking at.

### Services — "do the math"

- **`services/projections_service.py` → `ProjectionsService`** — takes the
  cleaned-up raw stats from `FfbProjectionsAdapter` and calculates fantasy
  point projections in all three scoring styles, using the shared rulebook in
  `scoring.py`. This answers "how many points will this player score." Only
  scores players who are part of the app's player universe (see below).
- **`services/roster_service.py` → `RosterService`** — decides which players
  the app actually cares about. See
  [How the player universe works](#how-the-player-universe-works-rosterservice)
  below — this is one of the more important concepts added recently.
- **`services/adp_comparison_service.py` → `AdpComparisonService`** — lines
  up ESPN, Yahoo, and Sleeper's draft rankings for the same set of players
  side by side, so you can compare where each platform ranks somebody.

### Presentation — "make it look right on screen"

- **`presentation/gamelog_view.py` → `GameLogView`** — decides which stat
  columns make sense to show for a game log, depending on position (a
  quarterback's table shows passing + rushing stats; a receiver's table shows
  receiving + rushing stats).
- **`presentation/table_style.py` → `style_table()`** — takes a data table and
  applies DataHuddle's visual style (dark header, alternating row shading,
  rounded numbers) before it's shown on a page. Used by both the Player
  Profile game log and the ADP Platform Comparison table.
- **`presentation/adp_comparison_view.py` → `AdpComparisonView`** — filters
  the ADP comparison table by position and picks/orders/labels the columns
  actually shown on screen.

### Tying it together

- **`app_context.py` → `AppContext`** — built once when the app starts up.
  Constructs one of everything above, in the right order, and holds onto them
  for the life of the running app. Every panel receives this one object
  instead of a handful of separate pieces.
- **`panels/player_profile.py`** — the Player Profile page: a dropdown to
  pick a player, their headshot, and their game log table. See the
  walkthrough below.
- **`panels/adp_comparison.py`** — the ADP Platform Comparison page: filter
  by position, toggle Half-PPR/Full-PPR, search by name, and browse a table
  comparing ESPN/Yahoo/Sleeper's rankings.

## Walkthrough: what happens when you open Player Profile

1. **App starts.** `app.py` builds one `AppContext`, which builds one of
   every repository/adapter/service described above.
2. **Page loads.** The player dropdown asks `RosterService` for the list of
   in-scope players (see [How the player universe works](#how-the-player-universe-works-rosterservice)
   below) and shows them (Chris Olave is the default pick).
3. **You pick a player.** The page now has that player's internal ID, and
   asks `PlayerDirectory` for three things: their headshot photo, their
   position, and their full game log.
4. **The game log gets shaped.** The raw game log (every stat, every column)
   gets handed to `GameLogView`, which trims it down to the columns that
   actually make sense for that player's position, sorted most-recent-first.
5. **The table gets styled.** That trimmed table is handed to `style_table()`,
   which applies DataHuddle's dark-header, alternating-row look.
6. **It's displayed.** The headshot and the finished table both show up on
   the page.

No step in this chain talks to MongoDB or the NFL stats service directly
except steps 2 and 3 (inside `RosterService`/`PlayerDirectory`) — every other
step just receives data and reshapes it, which is exactly the point: each
piece only does its own job.

## Walkthrough: what happens when you open ADP Platform Comparison

1. **App starts.** Same `AppContext` as above also builds `RosterService`,
   the three ADP adapters, and `AdpComparisonService`.
2. **Page loads.** You see a position filter, a Half-PPR/Full-PPR toggle, a
   name search box, and the table itself.
3. **The comparison gets built.** `AdpComparisonService` starts from
   `RosterService`'s roster (every UDK-ranked player) and attaches each
   platform's ADP value onto it where one exists — a player missing from a
   platform just shows a blank in that column rather than being left out.
4. **You filter/search.** Changing the position dropdown or typing a name
   narrows down the *already-built* comparison table — it doesn't rebuild
   the ESPN/Yahoo/Sleeper comparison from scratch, only the cheap filtering
   step re-runs.
5. **The table gets styled.** The filtered/shaped table is handed to the same
   `style_table()` used by Player Profile, for a consistent look.
6. **It's displayed.**

The Half-PPR/Full-PPR toggle *does* rebuild the comparison (ESPN and Sleeper
both have separate ADP numbers per format), which is why that step is kept
separate from the cheap position/search filtering — see the
[Class & Function Reference](#panelsadp_comparisonpy) for exactly how that
split works.

## How player identity resolution works (`player_id_map`)

This one deserves its own explanation, because it's not really a step in the
assembly line — it's more like a shared answer key that several stages reach
for whenever they need to combine data about the same player from more than
one source.

### The problem it solves

The same real player shows up differently in every data source:

- ESPN might list him as "Ja'Marr Chase"
- Sleeper might also say "Ja'Marr Chase" — but that's a coincidence, not a
  guarantee
- Yahoo might drop the apostrophe: "JaMarr Chase"
- Two *different* players could plausibly share an identical printed name

If you ever want to say "show me this player's ADP on three different
platforms side by side," or "rank my roster using our own projections but
show each player's Yahoo draft position too," the app needs one dependable
way to know that all of those rows are talking about the same person. That's
what `player_id_map` is for.

### What it looks like

It's a simple table with one row per (player, source) pairing:

| canonical_id  | source    | source_name       |
|---------------|-----------|-------------------|
| `00-0036355`  | `espn`    | Ja'Marr Chase     |
| `00-0036355`  | `sleeper` | Ja'Marr Chase     |
| `00-0036355`  | `yahoo`   | JaMarr Chase      |
| `00-0034796`  | `espn`    | Justin Jefferson  |

Every row says "on this source, this player is called this name, and here's
their permanent, unchanging ID." That ID — the `canonical_id` column — is not
the player's printed name. It's the internal ID the official NFL stats
service (`nflreadpy`) already uses for that player everywhere else in the
app (it's the same ID `PlayerDirectory` uses to look up headshots, positions,
and game logs). Anchoring to that instead of a name means the mapping never
breaks just because a name gets abbreviated, re-spelled, or happens to
collide with someone else's.

### Why it's filled in by hand, on purpose

It would be possible to have the computer *guess* matches automatically (e.g.
"these two names are 90% similar, probably the same guy"). That was
deliberately ruled out. An automatic guess that's wrong fails silently — two
different players get quietly merged, or a real match gets missed, and
nothing in the app tells you it happened. A manual mapping table fails
*loudly* instead: if a name has no entry yet, it simply doesn't show up in a
cross-source comparison, which is easy to notice and safe to leave alone
until someone adds the correct row.

### How it's used day to day

`PlayerIdentityRepo` (built and tested already, even though the underlying
table is still empty) offers three simple operations:

- **`resolve(source, name)`** — "what's the canonical ID for this one name
  from this one source?" Used for a single lookup.
- **`resolve_many(source, names)`** — the same thing, but for an entire
  column of names at once (e.g. every name in a freshly loaded ADP
  spreadsheet) — much faster than looking each one up individually.
- **`unresolved(source, names)`** — "which of these names have *no* match
  yet?" This is the important one: it's how the app surfaces exactly which
  names need a human to look at them, instead of silently dropping them.

### The day-to-day workflow, once this is in active use

1. You load a new batch of source data (say, a fresh Yahoo ADP export).
2. The loading code calls `unresolved()` and gets back a short list of names
   that don't have a `player_id_map` row yet for that source.
3. You look at that list and add one new row per name to
   `data/player_id_map.csv` (the same simple CSV-in, MongoDB-out pattern
   every other data file in this app already uses).
4. Re-run the loader; those names now resolve correctly from then on.
5. Repeat this occasionally as new players enter the league or a source
   changes how it spells someone's name.

### Where this fits in the bigger picture

Rather than being its own pipeline stage, `player_id_map` sits off to the
side as a shared reference that any stage can consult when it needs to line
up rows from different sources:

```
   ESPN names   ─┐
 Sleeper names   ─┼──▶  look up in player_id_map  ──▶  one shared ID per player
   Yahoo names   ─┘        (PlayerIdentityRepo)         (safe to join/compare on)
```

### Current status

The lookup class (`PlayerIdentityRepo`) is built and has been verified to
behave correctly, including the case where the table is completely empty
(today's reality — no player has been mapped yet). It becomes genuinely
useful the moment a feature that needs to compare across sources gets built
— **ADP Platform Comparison** will be the first one, per the roadmap, and
that's when populating `data/player_id_map.csv` by hand actually starts.

## How the player universe works (`RosterService`)

Every page so far — Player Profile's dropdown, ADP Platform Comparison,
projections — needs an answer to a basic question: **which players does
this app actually care about?** `RosterService` is the single place that
answers it.

### The problem it solves

`nflreadpy` (the NFL stats service) knows about roughly 25,000 players —
essentially everyone who's ever had an NFL roster spot, going back years,
including career backups, practice-squad players, and retirees. That's far
too broad for a fantasy football app: nobody wants to scroll past thousands
of irrelevant names to find this week's relevant options.

The fix: **UDK's rankings (Ultimate Draft Kit — QB/RB/WR/TE) are treated as
the definitive list of fantasy-relevant players.** If UDK doesn't rank a
player, the app doesn't show them — not in the Player Profile dropdown, not
in ADP Comparison, not in projections. This keeps every part of the app
focused on the roughly 300 players who actually matter for a given season.

### What it looks like

`RosterService` combines two things:

1. **`UdkRankingsAdapter`** reads UDK's four position-ranking collections
   (QB/RB/WR/TE — no kickers or defenses; UDK doesn't rank those, so neither
   does the app) and concatenates them into one list of ranked players.
2. **Identity resolution** (the same `player_id_map` → exact-name-match
   process described above) turns each UDK name into a stable `canonical_id`,
   so the roster can be joined against anything else in the app.

The result — `RosterService.roster()` — is one row per in-scope player, with
their canonical ID, name, headshot, position, and UDK ranking info (rank,
tier, risk/upside) all attached.

### Why rookies needed a second data source

UDK ranks rookies aggressively before the season even starts — that's the
whole point of pre-draft rankings. But `PlayerDirectory` used to be built
entirely on `nflreadpy`'s *game stats*, which only has rows for players who
have actually played an NFL game. A rookie with zero games played would
simply not exist in that data at all — not a name-matching failure, just
completely absent.

The fix: `PlayerDirectory` (and `NflReadRepo` underneath it) now also pulls
`nflreadpy`'s broader ~25,000-player reference table, which includes every
rostered player regardless of whether they've played yet. Name, headshot,
and position lookups use this broader table; game logs still use the
game-stats table, since only that one has actual game-by-game rows (a
rookie with no games simply has an empty game log, which the app already
handled gracefully).

### How it's used day to day

- **`roster()`** — the full table described above.
- **`canonical_ids()`** — just the set of IDs, for restricting some other
  data source down to "only players in scope."
- **`player_names()`** — a `{canonical_id: name}` mapping, ready to hand
  straight to a dropdown (this is what Player Profile's player picker uses).
- **`unresolved()`** — UDK names that couldn't be matched to a canonical ID
  at all (needs a manual `player_id_map` row, same workflow as above). In
  practice, roughly 96% of UDK's ~300 ranked players resolve automatically;
  the rest are almost always Jr./Sr./III suffix mismatches between UDK's
  naming and `nflreadpy`'s.

### How this changed ADP Comparison and Projections

Both features now start *from the roster* instead of starting from
whichever platforms happen to mention a player:

- **ADP Comparison** used to combine "anyone ESPN, Yahoo, or Sleeper
  mentions." Now it starts from the roster and attaches each platform's ADP
  where available — every UDK-ranked player shows up (blank ADP if a
  platform doesn't have them), and a platform's player who *isn't*
  UDK-ranked is excluded entirely.
- **Projections** (`ProjectionsService`) now resolves its own projected
  players to `canonical_id` too, and only keeps the ones that are also on
  the roster — so "our own projections" and "the players in the app" can
  never quietly drift apart into two different lists.

### Where this fits in the bigger picture

```
  UDK QB/RB/WR/TE rankings
            │
            ▼
   UdkRankingsAdapter ──▶ RosterService ──▶ canonical_id set / roster table
                                │                      │
                                │                      ├──▶ Player Profile dropdown
                                │                      ├──▶ ADP Comparison (base list)
                                │                      └──▶ Projections (scoping filter)
                                │
                     (uses PlayerIdentityRepo +
                      PlayerDirectory to resolve
                      UDK names → canonical_id)
```

## What's designed but not built yet

Your product roadmap (`PLANNING.md`) calls for several more features. The
architecture above already has a clear "slot" for each one — they just don't
have code yet:

- **Player notes & markings** ("Love/Like/Sleeper" tags, free-text notes) —
  will use the `db/documents.py` helper already built.
- **Team Depth Charts** — will rank players per team using
  `ProjectionsService`, which already exists and is tested.
- **Draft Plans** (saved, named draft boards) — will use `db/documents.py`
  plus a new service to calculate "is this player a good value at this
  pick."
- **DFS (Daily Fantasy) pages** — intentionally last on the roadmap; a couple
  of data-source decisions (salary data, play-by-play stats) still need to be
  made before these can be built.

**ADP Platform Comparison is now built** (see the walkthrough above and the
`AdpComparisonService`/`AdpComparisonView` reference entries below) — it's no
longer on this "not built yet" list.

## Why this is easier to extend

Two concrete examples of what "easy to expand" means in practice:

- **Adding a new stat-projection source** (say, a 4th projections website):
  write one new adapter file that cleans up that source's particular quirks,
  and nothing else in the app needs to change — every other layer already
  works with the clean, common format the adapter produces.
- **Adding a new page**: build its own small service if it needs new logic,
  wire it into `AppContext` alongside the existing pieces, and build the
  page. Existing pages are completely unaffected, since nothing is shared
  except through `AppContext` on purpose.

---

## Class & Function Reference

This section documents every class and function currently implemented, file
by file, in the order data flows through the pipeline. Where a parameter or
return value is a `pandas.DataFrame`, its expected/actual columns are listed
so the shape of the data at each step is explicit. Private methods (prefixed
with `_`) are internal implementation details of their class and are not
meant to be called from outside it, but are documented for completeness.

### `registry.py`

#### `class Collections`

A plain namespace of string constants — one per MongoDB collection. There is
no logic here; its only purpose is to give every collection name a single,
importable, typo-proof source of truth.

| Attribute              | Value                    | Status  |
|-------------------------|--------------------------|---------|
| `ESPN_PROJECTIONS`       | `"espn_projections"`     | Active  |
| `SLEEPER_PROJECTIONS`    | `"sleeper_projections"`  | Active  |
| `YAHOO_DRAFTANALYSIS`    | `"yahoo_draftanalysis"`  | Active  |
| `FFB_QB_PROJECTIONS`     | `"ffb_qb_projections"`   | Active  |
| `FFB_FLEX_PROJECTIONS`   | `"ffb_flex_projections"` | Active  |
| `UDK_QB_RANKINGS`        | `"udk_qb_rankings_ppr"`  | Active  |
| `UDK_RB_RANKINGS`        | `"udk_rb_rankings_ppr"`  | Active  |
| `UDK_WR_RANKINGS`        | `"udk_wr_rankings_ppr"`  | Active  |
| `UDK_TE_RANKINGS`        | `"udk_te_rankings_ppr"`  | Active  |
| `PLAYER_ID_MAP`          | `"player_id_map"`        | Planned — no collection yet |
| `PLAYER_NOTES`           | `"player_notes"`         | Planned — no collection yet |
| `PLAYER_CATEGORIES`      | `"player_categories"`    | Planned — no collection yet |
| `ADP_ESPN`               | `"adp_espn"`             | Planned — no collection yet |
| `ADP_YAHOO`              | `"adp_yahoo"`            | Planned — no collection yet |
| `ADP_SLEEPER`            | `"adp_sleeper"`          | Planned — no collection yet |
| `DRAFT_PLANS`            | `"draft_plans"`          | Planned — no collection yet |

#### `class Position(str, Enum)`

Enumerates the standard fantasy-football position labels used throughout the
app: `QB`, `RB`, `WR`, `TE`, `K`, `DST`. Because it subclasses `str`, a
`Position` member can be compared directly against a plain string (e.g.
`Position.QB == "QB"` is `True`), which keeps it convenient to use alongside
data that hasn't been converted to the enum.

#### `parse_positions(raw: str) -> list[Position]`

- **Purpose:** Splits a comma-separated, dual-eligibility position string
  (as Yahoo's data provides, e.g. `"RB,TE"`) into a list of `Position` values.
- **Parameters:**
  - `raw` (`str`) — one or more position tokens separated by commas, e.g.
    `"RB,TE"` or `"DEF"`. Whitespace around tokens is tolerated.
- **Returns:** `list[Position]` — one entry per token that could be matched to
  a known position.
- **Notes:** The token `"DEF"` is normalized to `Position.DST` before lookup
  (Yahoo's own label for defense/special teams). Any token that still doesn't
  match a known `Position` value is silently skipped rather than raising an
  exception — this function is designed to tolerate unexpected input from
  external data sources.

---

### `scoring.py`

#### Module-level constants

The default point values used by the scoring formula, expressed as plain
numbers so they can be referenced individually if needed:

| Constant                      | Value | Meaning                                |
|--------------------------------|-------|-----------------------------------------|
| `GAMES_PER_SEASON`              | `17`  | Used to convert season totals to per-game averages |
| `PASSING_YARDS_PER_POINT`       | `25`  | 1 point awarded per 25 passing yards    |
| `RUSHING_YARDS_PER_POINT`       | `10`  | 1 point awarded per 10 rushing yards    |
| `RECEIVING_YARDS_PER_POINT`     | `10`  | 1 point awarded per 10 receiving yards  |
| `PASSING_TD_POINTS`             | `4`   | Points per passing touchdown            |
| `RUSHING_TD_POINTS`             | `6`   | Points per rushing touchdown            |
| `RECEIVING_TD_POINTS`           | `6`   | Points per receiving touchdown          |
| `INTERCEPTION_POINTS`           | `-2`  | Point penalty per interception thrown   |
| `FUMBLE_LOST_POINTS`            | `-2`  | Point penalty per fumble lost           |

#### `STAT_KEYS`

A tuple of the nine stat field names every call to `fantasy_points()` must
supply: `passing_yards`, `passing_tds`, `interceptions`, `rushing_yards`,
`rushing_tds`, `receiving_yards`, `receiving_tds`, `receptions`,
`fumbles_lost`.

#### `class ScoringFormat(str, Enum)`

Enumerates the three supported scoring styles: `REGULAR` (`"regular"`),
`HALF_PPR` (`"half_ppr"`), `FULL_PPR` (`"full_ppr"`). "PPR" stands for
"points per reception" — the only rule that differs between these three
formats is how many points a catch is worth.

#### `class ScoringRules` *(frozen dataclass)*

- **Purpose:** Bundles every per-stat point value needed to score one
  scoring format into a single, immutable object. "Frozen" means that once
  created, its fields cannot be changed — this prevents one part of the
  code from accidentally altering scoring rules that another part relies on.
- **Fields:**
  - `reception_points` (`float`, **required**) — the one value that actually
    differs between formats (`0` for regular, `0.5` for half-PPR, `1` for
    full-PPR).
  - `passing_yards_per_point`, `rushing_yards_per_point`,
    `receiving_yards_per_point` (`float`, default `25`/`10`/`10`)
  - `passing_td_points`, `rushing_td_points`, `receiving_td_points`
    (`float`, default `4`/`6`/`6`)
  - `interception_points`, `fumble_lost_points` (`float`, default `-2`/`-2`)
  - `games_per_season` (`int`, default `17`)

#### `SCORING_RULES`

A dictionary, `dict[ScoringFormat, ScoringRules]`, mapping each scoring
format to its pre-built `ScoringRules` instance. This is the table
`fantasy_points()` looks up rules from — adding a new scoring format means
adding one new entry here.

#### `fantasy_points(stats, fmt) -> number or pandas.Series`

- **Purpose:** Computes total season fantasy points for one scoring format
  from a set of raw counting stats.
- **Parameters:**
  - `stats` (`Mapping[str, T]`) — must provide every key listed in
    `STAT_KEYS`. `T` can be a plain Python number (for scoring a single
    player) or a `pandas.Series` (for scoring an entire column of players
    at once — this is called "vectorized" computation).
  - `fmt` (`ScoringFormat`) — which scoring format's rules to apply.
- **Returns:** the same type as the values in `stats` — either a single
  number or a `pandas.Series` — representing total fantasy points for the
  season.
- **Notes:** The order the formula's terms are added in is fixed
  intentionally, so that results are bit-for-bit reproducible against the
  original formula this replaced.

#### `fantasy_points_all_formats(stats) -> dict`

- **Purpose:** Convenience function that calls `fantasy_points()` once for
  each of the three `ScoringFormat` values.
- **Parameters:** `stats` — same shape as above.
- **Returns:** `dict[ScoringFormat, T]` — one entry per scoring format, each
  holding that format's season-total fantasy points.

#### `per_game(points, games=GAMES_PER_SEASON) -> number or pandas.Series`

- **Purpose:** Converts a season-total point value into a per-game average.
- **Parameters:** `points` (`T`) — a season total; `games` (`int`, default
  `17`) — number of games to divide by.
- **Returns:** `T` — `points / games`.

---

### `db/` package

The `db/` package is the only part of the app that talks to MongoDB
directly. Every function below either reads from or writes to the database
named `"data-huddle"`.

#### `db/connection.py`

##### `get_client() -> pymongo.MongoClient`

- **Purpose:** Returns a MongoDB client connection. Creates it on the first
  call and reuses the same connection on every subsequent call (a
  "singleton" pattern), so the app doesn't open a new database connection
  for every query.
- **Returns:** a `pymongo.MongoClient` instance.
- **Notes:** Reads the connection string from the `MONGODB_URI` environment
  variable. Raises `KeyError` if that variable is not set.

##### `get_db() -> pymongo.database.Database`

- **Purpose:** Returns the handle to the app's specific database
  (`"data-huddle"`) within the MongoDB connection.
- **Returns:** a `pymongo.database.Database` object — this is what every
  other function in `db/` uses to access individual collections (e.g.
  `get_db()["espn_projections"]`).

#### `db/reader.py`

##### `read_collection(collection_name: str) -> pandas.DataFrame`

- **Purpose:** Reads every document in a MongoDB collection into a single
  DataFrame — a full "bulk dump" of that collection's current contents.
- **Parameters:** `collection_name` (`str`) — the collection to read.
- **Returns:** `pandas.DataFrame`, one row per document, with MongoDB's
  internal `_id` field excluded. If the collection is empty or doesn't
  exist, returns an empty DataFrame rather than raising an error.

#### `db/loader.py`

##### `reload_collection(collection_name: str, df: pandas.DataFrame) -> int`

- **Purpose:** Replaces a collection's entire contents with the rows of a
  DataFrame — deletes everything currently in the collection, then inserts
  the new rows ("wipe-and-replace"). This is what keeps MongoDB in sync
  with the CSV files in `data/`.
- **Parameters:**
  - `collection_name` (`str`) — the collection to replace.
  - `df` (`pandas.DataFrame`) — the new contents; each row becomes one
    MongoDB document.
- **Returns:** `int` — the number of rows written.
- **Notes:** If `df` is empty, the collection is left empty (no insert is
  attempted, since MongoDB's insert operation doesn't accept an empty list).

##### `reload_collection_from_csv(collection_name: str, csv_path) -> int`

- **Purpose:** Convenience wrapper for the common case of loading a
  collection directly from a CSV file on disk.
- **Parameters:** `collection_name` (`str`); `csv_path` (`str` or `Path`) —
  location of the CSV file.
- **Returns:** `int` — the number of rows written (delegates to
  `reload_collection()` after reading the CSV with `pandas.read_csv`).

#### `db/documents.py`

Unlike `reader.py`/`loader.py` (which operate on a whole collection at
once), these four functions operate on individual documents — used for
data that's read/written one record at a time, like a single player's note.

##### `find_one(collection_name: str, filter: dict) -> dict | None`

- **Purpose:** Retrieves a single document matching a query filter.
- **Parameters:** `collection_name` (`str`); `filter` (`dict`) — a MongoDB
  query, e.g. `{"canonical_id": "00-0036355"}`.
- **Returns:** `dict` representing the matched document (`_id` excluded),
  or `None` if nothing matches.

##### `find_all(collection_name: str, filter: dict = None) -> list[dict]`

- **Purpose:** Retrieves every document matching a query filter.
- **Parameters:** `collection_name` (`str`); `filter` (`dict`, optional) —
  defaults to matching every document in the collection if omitted.
- **Returns:** `list[dict]` — one entry per matching document.

##### `upsert(collection_name: str, filter: dict, doc: dict) -> None`

- **Purpose:** Updates the fields of a matching document, or inserts a new
  document if no match exists yet (an "upsert" — update-or-insert).
- **Parameters:** `collection_name` (`str`); `filter` (`dict`) — identifies
  which document to update; `doc` (`dict`) — the fields to set.
- **Returns:** `None`.

##### `delete(collection_name: str, filter: dict) -> None`

- **Purpose:** Removes the first document matching a query filter.
- **Parameters:** `collection_name` (`str`); `filter` (`dict`).
- **Returns:** `None`.

---

### `repositories/` package

#### `class CollectionRepo` (`repositories/collection_repo.py`)

- **Purpose:** A generic, reusable wrapper that reads one MongoDB
  collection in bulk and caches the result in memory, so the same
  collection is never fetched from the database more than once unless
  explicitly told to.
- **Constructor:** `__init__(self, collection_name: str)`
  - `collection_name` (`str`) — the MongoDB collection this instance will
    read from. No data is fetched yet at construction time (the fetch is
    "lazy" — deferred until first use).
- **Public attributes:** `collection_name` (`str`) — stored as given.
- **Methods:**
  - **`read(self) -> pandas.DataFrame`**
    - **Purpose:** Returns the collection's contents. On the very first
      call, fetches from MongoDB via `db.reader.read_collection()`; every
      call after that returns the cached copy already in memory instead of
      querying the database again.
    - **Returns:** `pandas.DataFrame`.
  - **`refresh(self) -> pandas.DataFrame`**
    - **Purpose:** Forces a fresh fetch from MongoDB, overwriting whatever
      was cached. Use this after re-running `scripts/load_data.py` if you
      want the running app to pick up new data without a full restart.
    - **Returns:** `pandas.DataFrame` — the newly fetched data.

#### `class NflReadRepo` (`repositories/nfl_read_repo.py`)

- **Purpose:** Wraps the `nflreadpy` library — the app's one data source
  that is not MongoDB-backed. Wraps *two* separate pulls (see Methods
  below), each the heaviest load of its kind in the app, so each is loaded
  once and kept in memory for as long as the app runs.
- **Constructor:** `__init__(self, seasons: list)`
  - `seasons` (`list[int]`) — which NFL season years `player_stats()` should
    load, e.g. `[2020, 2021, 2022, 2023, 2024, 2025]`. Does not affect
    `players()`, which is season-independent.
- **Methods:**
  - **`player_stats(self) -> pandas.DataFrame`**
    - **Purpose:** Returns the full player-stats table across every
      configured season. Loads from `nflreadpy` on the first call only;
      later calls return the cached copy.
    - **Returns:** `pandas.DataFrame` — one row per player per game played,
      including columns such as `player_id`, `player_display_name`,
      `position`, `team`, `week`, `season`, and dozens of statistical
      columns (passing/rushing/receiving stats, etc.). Only players who
      have played at least one recorded game appear here.
  - **`players(self) -> pandas.DataFrame`**
    - **Purpose:** Returns a broad player reference — every player who's
      ever had an NFL roster spot (~25,000 rows), regardless of whether
      they've played a game. Loads from `nflreadpy` on the first call only;
      later calls return the cached copy.
    - **Returns:** `pandas.DataFrame` with columns including `gsis_id`
      (the same stable ID as `player_stats()`'s `player_id`),
      `display_name`, `position`, `headshot`, `latest_team`, `status`.
    - **Notes:** This is what makes rookies with zero games played
      resolvable — `player_stats()` alone would have no record of them at
      all, since it only contains rows for players who've actually played.
  - **`refresh(self, seasons: list = None) -> pandas.DataFrame`**
    - **Purpose:** Forces a fresh load of `player_stats()` from `nflreadpy`.
    - **Parameters:** `seasons` (`list[int]`, optional) — if provided,
      replaces the previously configured season list before reloading; if
      omitted, reloads using whichever seasons were already configured.
    - **Returns:** `pandas.DataFrame` — same shape as `player_stats()`.
  - **`refresh_players(self) -> pandas.DataFrame`**
    - **Purpose:** Forces a fresh load of `players()` from `nflreadpy`.
    - **Returns:** `pandas.DataFrame` — same shape as `players()`.

#### `class PlayerDirectory` (`repositories/player_directory.py`)

- **Purpose:** Provides every player identity/lookup operation the app
  needs — searching for players, and looking up a specific player's name,
  headshot, position, team, or game log — all keyed by `canonical_id`
  (nflreadpy's stable ID, `gsis_id` in `NflReadRepo.players()` / `player_id`
  in `NflReadRepo.player_stats()` — confirmed the same value/format in both).
- **Constructor:** `__init__(self, nfl_read_repo: NflReadRepo)`
  - `nfl_read_repo` (`NflReadRepo`) — the repository this class pulls its
    underlying data from.
- **Module constant:** `DEFAULT_HEADSHOT = "www/defaultPlayer.png"` — the
  fallback image path returned when no headshot URL is available for a
  player.
- **Data source split:** `search_names()`, `get_display_name()`,
  `get_headshot()`, `get_position()`, `get_team()`, and
  `resolve_by_display_name()` all read from `nfl_read_repo.players()` — the
  broad ~25,000-player reference, which includes rookies with zero games
  played. Only `get_gamelog()` reads from `nfl_read_repo.player_stats()`,
  since only that source has per-game rows.
- **Methods:**
  - **`search_names(self, query: str = "", positions: list = None) -> dict`**
    - **Purpose:** Returns the full set of players nflreadpy knows about
      (not scoped to the app's roster — see `RosterService.player_names()`
      for the roster-scoped equivalent Player Profile's dropdown actually
      uses today).
    - **Parameters:**
      - `query` (`str`, optional) — a case-insensitive substring to filter
        display names by (e.g. `"chase"` would match "Ja'Marr Chase").
        Empty string (the default) returns all players.
      - `positions` (`list[str]`, optional) — if provided, restricts
        results to players whose position is in this list.
    - **Returns:** `dict[str, str]` mapping `canonical_id → display_name`,
      one entry per unique player, sorted alphabetically by display name.
      This shape is chosen specifically because it can be passed directly
      as the `choices` argument to a Shiny dropdown control.
  - **`get_display_name(self, canonical_id: str) -> str | None`**
    - **Purpose:** Looks up one player's display name.
    - **Returns:** `str`, or `None` if the ID has no matching rows.
  - **`get_headshot(self, canonical_id: str) -> str`**
    - **Purpose:** Looks up one player's headshot image URL.
    - **Returns:** `str` — the image URL if one is on record, otherwise
      `DEFAULT_HEADSHOT`. Unlike the other lookup methods, this one never
      returns `None`, since the calling page always needs *some* image to
      display.
  - **`get_position(self, canonical_id: str) -> str | None`**
    - **Purpose:** Looks up a player's most recently recorded position
      (e.g. `"WR"`).
    - **Returns:** `str`, or `None` if not found.
  - **`get_team(self, canonical_id: str) -> str | None`**
    - **Purpose:** Looks up a player's most recently recorded team (e.g.
      `"NO"`).
    - **Returns:** `str`, or `None` if not found.
  - **`get_gamelog(self, canonical_id: str) -> pandas.DataFrame`**
    - **Purpose:** Returns every game a player has a recorded stat line for,
      across every season `NflReadRepo` was configured to load.
    - **Returns:** `pandas.DataFrame` — one row per game, with the same
      columns as `NflReadRepo.player_stats()`; empty DataFrame if the
      player has no games on record (e.g. a rookie who hasn't played yet).
  - **`resolve_by_display_name(self, names: pandas.Series, positions: pandas.Series = None) -> pandas.Series`**
    - **Purpose:** The exact-match identity fallback described in
      [How player identity resolution works](#how-player-identity-resolution-works-player_id_map)
      — matches source-specific player names against this broad player
      reference when `player_id_map` doesn't have an entry yet.
    - **Parameters:** `names` (`pandas.Series` of `str`) — names to resolve;
      `positions` (`pandas.Series` of `str`, optional) — if provided, both
      name AND position must match (reduces same-name collision risk).
    - **Returns:** `pandas.Series`, same index as `names`, each entry
      replaced by its `canonical_id` or `NaN` if no exact match was found.
    - **Notes:** Matching is case/whitespace-normalized but otherwise exact
      — not fuzzy/similarity-based, matching the same philosophy as
      `player_id_map` itself.

#### `class PlayerIdentityRepo` (`repositories/player_identity_repo.py`)

See also the dedicated [How player identity resolution works](#how-player-identity-resolution-works-player_id_map)
section above for the conceptual explanation — this entry documents the
exact method signatures.

- **Purpose:** Resolves a source-specific player name (as spelled by ESPN,
  Yahoo, Sleeper, UDK, etc.) to the stable `canonical_id`, by looking it up
  in the manually curated `player_id_map` collection — and, via the
  `_with_fallback` methods, falling back to an exact name match when
  `player_id_map` doesn't have an entry yet.
- **Constructor:** `__init__(self, collection_repo: CollectionRepo, player_directory: PlayerDirectory)`
  - `collection_repo` (`CollectionRepo`) — expected to already be
    configured to point at `Collections.PLAYER_ID_MAP`.
  - `player_directory` (`PlayerDirectory`) — supplies the exact-name-match
    fallback (`resolve_by_display_name()`) used by the `_with_fallback`
    methods below.
- **Methods:**
  - **`resolve(self, source: str, source_name: str) -> str | None`**
    - **Purpose:** Looks up the canonical ID for one name from one source,
      checking `player_id_map` only (no fallback).
    - **Parameters:** `source` (`str`) — the data source the name came from
      (e.g. `"espn"`, `"yahoo"`, `"sleeper"`, `"udk"`, `"ffb"`); `source_name`
      (`str`) — the exact name string as that source spells it.
    - **Returns:** `str` (the `canonical_id`), or `None` if no mapping row
      exists yet for that source/name pair.
  - **`resolve_many(self, source: str, names: pandas.Series) -> pandas.Series`**
    - **Purpose:** The vectorized equivalent of `resolve()` — translates an
      entire column of names in one call, checking `player_id_map` only.
    - **Parameters:** `source` (`str`); `names` (`pandas.Series` of `str`)
      — a column of source-specific names, e.g. a `name` column from a
      freshly loaded ADP DataFrame.
    - **Returns:** `pandas.Series`, same length and index as `names`, with
      each name replaced by its `canonical_id`. Names with no mapping row
      come back as `None`/`NaN` at that position rather than being dropped.
  - **`unresolved(self, source: str, names: Iterable[str]) -> list`**
    - **Purpose:** Identifies which names from a given source have *no*
      `player_id_map` row yet (no fallback applied).
    - **Parameters:** `source` (`str`); `names` (any iterable of `str`) —
      candidate names to check.
    - **Returns:** `list[str]` — the names with no match, de-duplicated,
      in the order they first appeared in `names`.
  - **`resolve_many_with_fallback(self, source: str, names: pandas.Series, positions: pandas.Series = None) -> pandas.Series`**
    - **Purpose:** The resolution method actually used throughout the app
      today — tries `resolve_many()` (the manually-curated table) first,
      then falls back to `player_directory.resolve_by_display_name()` for
      anything still unresolved. Centralizes the two-step "curated mapping,
      then exact-name match" logic in one place, shared by
      `AdpComparisonService`, `ProjectionsService`, and `RosterService`.
    - **Parameters:** `source` (`str`); `names` (`pandas.Series` of `str`);
      `positions` (`pandas.Series` of `str`, optional) — passed through to
      the fallback to reduce same-name collision risk.
    - **Returns:** `pandas.Series`, same shape as `resolve_many()`'s.
  - **`unresolved_with_fallback(self, source: str, names: pandas.Series, positions: pandas.Series = None) -> list`**
    - **Purpose:** Like `unresolved()`, but for names failing *both*
      `player_id_map` and the exact-name fallback — the true "needs a
      human to look at this" list.
    - **Returns:** `list[str]` — de-duplicated, in first-seen order.

---

### `adapters/` package

#### `class FfbProjectionsAdapter` (`adapters/ffb_projections_adapter.py`)

- **Purpose:** Converts Fantasy Footballers/UDK's two raw projection
  collections (one for quarterbacks, one for RB/WR/TE — the "flex"
  positions) into a single combined DataFrame using this app's canonical
  column names. This is where the source's spreadsheet quirks are isolated
  and cleaned up.
- **Module constant:** `STAT_COLUMNS` — the ten canonical stat column names
  this adapter's output always includes: `passing_yards`, `passing_tds`,
  `interceptions`, `rushing_attempts`, `rushing_yards`, `rushing_tds`,
  `receptions`, `receiving_yards`, `receiving_tds`, `fumbles_lost`.
- **Constructor:** `__init__(self, qb_collection_repo, flex_collection_repo)`
  - `qb_collection_repo` (`CollectionRepo`) — expected to point at
    `Collections.FFB_QB_PROJECTIONS`.
  - `flex_collection_repo` (`CollectionRepo`) — expected to point at
    `Collections.FFB_FLEX_PROJECTIONS`.
- **Methods:**
  - **`load(self) -> pandas.DataFrame`**
    - **Purpose:** The adapter's main entry point. Reads both source
      collections, normalizes each into the canonical schema, combines
      them into one table, and fills in `0` for any of `STAT_COLUMNS` that
      a given row doesn't have (e.g. a QB row has no `receptions` value).
    - **Returns:** `pandas.DataFrame` with columns `name`, `team`,
      `bye_week`, `position`, `rank`, plus every column in `STAT_COLUMNS`
      — one row per projected player. Contains raw stats only; fantasy
      points are not calculated here (see `ProjectionsService` below).
  - **`_normalize_qb(self, df: pandas.DataFrame) -> pandas.DataFrame`**
    *(private)*
    - **Purpose:** Renames the raw UDK quarterback collection's columns
      into the canonical schema. Because the source file doesn't include
      an explicit position column, `position` is hardcoded to `"QB"` for
      every row. The source file's duplicate `YDS`/`TDS` headers (pandas
      renames the second occurrence to `YDS.1`/`TDS.1` on read) are mapped
      explicitly: `YDS`/`TDS` become `passing_yards`/`passing_tds`, while
      `YDS.1`/`TDS.1` become `rushing_yards`/`rushing_tds`.
    - **Returns:** `pandas.DataFrame`, or an empty DataFrame if the input
      was empty.
  - **`_normalize_flex(self, df: pandas.DataFrame) -> pandas.DataFrame`**
    *(private)*
    - **Purpose:** Same idea, for the RB/WR/TE ("flex") collection.
      `position` is read directly from the source's own `Pos` column
      (since this file covers three positions, not just one). Here,
      `YDS`/`TDS` map to `rushing_yards`/`rushing_tds`, while `YDS.1`/
      `TDS.1` map to `receiving_yards`/`receiving_tds` — the opposite
      mapping from `_normalize_qb`, because the two source files list
      their stat categories in a different order.
    - **Returns:** `pandas.DataFrame`, or an empty DataFrame if the input
      was empty.

#### `class UdkRankingsAdapter` (`adapters/udk_rankings_adapter.py`)

- **Purpose:** Reads UDK's four position-ranking collections (QB, RB, WR,
  TE — all four share an identical schema, unlike `FfbProjectionsAdapter`'s
  two differently-shaped sources) and concatenates them into one canonical
  DataFrame. This is the source of truth for "which players is the app
  scoped to" — see `RosterService` below.
- **Constructor:** `__init__(self, qb_collection_repo, rb_collection_repo, wr_collection_repo, te_collection_repo)`
  - Four `CollectionRepo` instances, expected to point at
    `Collections.UDK_QB_RANKINGS`, `UDK_RB_RANKINGS`, `UDK_WR_RANKINGS`, and
    `UDK_TE_RANKINGS` respectively.
- **Methods:**
  - **`load(self) -> pandas.DataFrame`**
    - **Purpose:** Reads and combines all four collections.
    - **Returns:** `pandas.DataFrame` with columns `name`, `position`,
      `team`, `bye_week`, `rank`, `points`, `risk`, `upside`, `adp`, `tier`
      — one row per UDK-ranked player, no identity resolution applied yet
      (that happens in `RosterService`).

#### `class AdpSourceAdapter` *(Protocol)* and `EspnAdpAdapter`, `SleeperAdpAdapter`, `YahooAdpAdapter` (`adapters/adp_source_adapter.py`)

- **Purpose:** `AdpSourceAdapter` is a `Protocol` (a documented shape, not an
  actual parent class — nothing inherits from it) describing what every ADP
  platform adapter must provide: a `load(fmt)` method returning `name`,
  `team`, `position`, `adp` columns. The three concrete classes each expose
  one platform's ADP data in that identical shape, so
  `AdpComparisonService` never needs to special-case which platform it's
  reading.
- **`EspnAdpAdapter.__init__(self, collection_repo)`** /
  **`SleeperAdpAdapter.__init__(self, collection_repo)`**
  - `collection_repo` (`CollectionRepo`) — expected to point at
    `Collections.ESPN_PROJECTIONS` / `Collections.SLEEPER_PROJECTIONS`
    respectively. Both platforms' projection collections already carry
    separate `half_ppr_adp`/`full_ppr_adp` columns.
  - **`load(self, fmt: ScoringFormat) -> pandas.DataFrame`** — picks
    `half_ppr_adp` or `full_ppr_adp` based on `fmt`, returns
    `name, team, position, adp`.
- **`YahooAdpAdapter.__init__(self, collection_repo)`**
  - `collection_repo` (`CollectionRepo`) — expected to point at
    `Collections.YAHOO_DRAFTANALYSIS`.
  - **`load(self, fmt: ScoringFormat) -> pandas.DataFrame`** — `fmt` is
    accepted (to match the shared shape) but ignored; Yahoo only publishes
    one ADP value regardless of scoring format. Returns
    `name, team, position, adp`.

---

### `services/` package

#### `class ProjectionsService` (`services/projections_service.py`)

- **Purpose:** Answers "how many fantasy points will this player score,"
  by combining an adapter's raw-stat output with the scoring rules defined
  in `scoring.py`. This is the only place in the app where projected stats
  and the scoring formula are brought together. Also resolves each
  projected player to a `canonical_id` and scopes the result down to
  `RosterService`'s roster, so this never shows a player the rest of the
  app doesn't also know about.
- **Constructor:** `__init__(self, ffb_adapter: FfbProjectionsAdapter, identity_repo: PlayerIdentityRepo, roster_service: RosterService)`
  - `ffb_adapter` (`FfbProjectionsAdapter`) — supplies the raw stats this
    service scores.
  - `identity_repo` (`PlayerIdentityRepo`) — resolves FFB's player names to
    `canonical_id` (source `"ffb"`), via `resolve_many_with_fallback()`.
  - `roster_service` (`RosterService`) — supplies the set of in-scope
    `canonical_id`s to filter down to.
- **Methods:**
  - **`get_own_projections(self) -> pandas.DataFrame`**
    - **Purpose:** Returns the app's own fantasy point projections — as
      opposed to a vendor's pre-computed projections (like ESPN's or
      Sleeper's) — computed from Fantasy Footballers/UDK raw stats. Per
      the product roadmap, these are the projections used for Team Depth
      Chart ordering and Draft Plan "True Value."
    - **Returns:** `pandas.DataFrame` — every column produced by
      `FfbProjectionsAdapter.load()`, plus `canonical_id` and six new
      columns: `fantasy_points_regular_season`,
      `fantasy_points_regular_per_game`, `fantasy_points_half_ppr_season`,
      `fantasy_points_half_ppr_per_game`, `fantasy_points_full_ppr_season`,
      `fantasy_points_full_ppr_per_game`. Only includes players resolved to
      a `canonical_id` that's also in `RosterService`'s roster.
  - **`unresolved(self) -> list`**
    - **Purpose:** FFB projection names that couldn't be matched to a
      `canonical_id` at all (via `player_id_map` or the exact-name
      fallback) — candidates for a manual `player_id_map` row.
    - **Returns:** `list[str]`, de-duplicated.

#### `class RosterService` (`services/roster_service.py`)

See also [How the player universe works](#how-the-player-universe-works-rosterservice)
above for the conceptual explanation — this entry documents the exact
method signatures.

- **Purpose:** Defines the app's player universe — only players ranked in
  UDK's QB/RB/WR/TE rankings are in scope. Combines `UdkRankingsAdapter`
  with identity resolution to produce a roster keyed by `canonical_id`.
- **Constructor:** `__init__(self, udk_rankings_adapter: UdkRankingsAdapter, identity_repo: PlayerIdentityRepo, player_directory: PlayerDirectory)`
- **Methods:**
  - **`roster(self) -> pandas.DataFrame`**
    - **Purpose:** The full in-scope roster.
    - **Returns:** `pandas.DataFrame` with columns `canonical_id`,
      `display_name`, `headshot_url`, `position`, `team`, `bye_week`,
      `rank`, `points`, `risk`, `upside`, `adp`, `tier` — one row per
      UDK-ranked player successfully resolved to a `canonical_id`. If a
      player is ranked in more than one position file by mistake, only the
      best (lowest) rank is kept.
  - **`canonical_ids(self) -> set`**
    - **Purpose:** Just the bare set of in-scope IDs, for restricting other
      data sources (e.g. `ProjectionsService`) down to roster players only.
    - **Returns:** `set[str]`.
  - **`player_names(self) -> dict`**
    - **Purpose:** The roster-scoped equivalent of
      `PlayerDirectory.search_names()` — what Player Profile's dropdown
      actually uses.
    - **Returns:** `dict[str, str]` mapping `canonical_id → display_name`.
  - **`unresolved(self) -> list`**
    - **Purpose:** UDK player names that couldn't be matched to a
      `canonical_id` at all.
    - **Returns:** `list[str]`, de-duplicated.

#### `class AdpComparisonService` (`services/adp_comparison_service.py`)

- **Purpose:** Builds the ADP Platform Comparison table — one row per
  roster player, with each of ESPN/Yahoo/Sleeper's ADP attached where
  available. Driven by `RosterService`, not by "whoever a platform happens
  to mention" — see [How the player universe works](#how-the-player-universe-works-rosterservice)
  for why.
- **Constructor:** `__init__(self, espn_adapter, sleeper_adapter, yahoo_adapter, identity_repo: PlayerIdentityRepo, roster_service: RosterService)`
  - The three ADP adapters, plus `identity_repo` (resolves each platform's
    player names to `canonical_id`) and `roster_service` (supplies the base
    roster and each player's display name/headshot/position).
- **Methods:**
  - **`compare(self, fmt: ScoringFormat) -> pandas.DataFrame`**
    - **Purpose:** Builds the full comparison table for one scoring format.
    - **Parameters:** `fmt` (`ScoringFormat`) — `HALF_PPR` or `FULL_PPR`,
      passed through to the ESPN/Sleeper adapters (Yahoo ignores it).
    - **Returns:** `pandas.DataFrame` with one row per roster player:
      `canonical_id`, `display_name`, `headshot_url`, `position`,
      `espn_adp`, `yahoo_adp`, `sleeper_adp`. Uses a left join *from* the
      roster — every roster player appears even with 0 platforms' ADP
      (blank in those columns); a platform's player who isn't on the
      roster never enters the result.
  - **`unresolved(self, source: str) -> list`**
    - **Purpose:** Player names from one platform that couldn't be matched
      to a `canonical_id` at all.
    - **Parameters:** `source` (`str`) — `"espn"`, `"sleeper"`, or
      `"yahoo"`.
    - **Returns:** `list[str]`, de-duplicated.
  - **`_prepare(self, source, df, adp_col)`** *(private)*
    - **Purpose:** Resolves one platform's raw rows to `canonical_id` and
      collapses any accidental duplicate rows per player (keeping the
      lower/better ADP).
    - **Returns:** `pandas.DataFrame` with columns `canonical_id`,
      `<adp_col>` — unresolved names are dropped (not shown blank), since
      they have no `canonical_id` to join on.

---

### `presentation/` package

#### `class GameLogView` (`presentation/gamelog_view.py`)

- **Purpose:** Decides which statistical columns are relevant to display
  in a player's game log table, based on their position, and sorts the
  result most-recent-first. Contains no data-fetching or scoring logic —
  purely a display-shaping step.
- **Class constants** (each a `list[str]` of column names):
  - `BASIC_COLS` — `week`, `season`, `team`, `opponent_team` (shown for
    every position).
  - `PASSING_COLS` — `attempts`, `completions`, `passing_yards`,
    `passing_tds`, `passing_interceptions`, `passing_2pt_conversions`,
    `sack_fumbles_lost`.
  - `RUSHING_COLS` — `carries`, `rushing_yards`, `rushing_tds`,
    `rushing_fumbles_lost`, `rushing_2pt_conversions`.
  - `RECEIVING_COLS` — `receptions`, `targets`, `receiving_yards`,
    `receiving_tds`, `receiving_fumbles_lost`, `receiving_2pt_conversions`.
- **Methods:**
  - **`shape(cls, gamelog_data: pandas.DataFrame, position: str = None) -> pandas.DataFrame`**
    *(class method — called as `GameLogView.shape(...)`, without creating an
    instance)*
    - **Purpose:** Trims a raw game log down to the columns relevant for
      the given position, and sorts it by most recent game first.
    - **Parameters:**
      - `gamelog_data` (`pandas.DataFrame`) — raw game-by-game rows, such
        as the output of `PlayerDirectory.get_gamelog()`.
      - `position` (`str`, optional) — the player's position. Determines
        which column group is shown:

        | `position` value      | Columns shown                          |
        |------------------------|------------------------------------------|
        | `"QB"`                 | Basic + Passing + Rushing                |
        | `"RB"`                 | Basic + Rushing + Receiving               |
        | any other non-`None` value | Basic + Receiving + Rushing (used for WR/TE/etc.) |
        | `None`                 | Basic columns only                        |
    - **Returns:** `pandas.DataFrame`, restricted to whichever of the
      relevant columns actually exist in the input, sorted by `season` and
      `week` descending (most recent game first). Returns an empty
      DataFrame if `gamelog_data` was empty.

#### `style_table(df, col_mapping=None)` (`presentation/table_style.py`)

- **Purpose:** Applies DataHuddle's visual styling — a dark header row,
  alternating row shading, a hover highlight, and rounded numeric values —
  to a DataFrame, preparing it for display in a Shiny table output.
- **Parameters:**
  - `df` (`pandas.DataFrame`) — the data to style.
  - `col_mapping` (`dict`, optional) — maps original column names to
    display labels for the header row only (e.g. `{"rushing_yards": "Rush
    Yds"}`); the underlying data and column names are unaffected.
- **Returns:** a `pandas.io.formats.style.Styler` object — Shiny's
  `@render.table` can display this directly — or the original DataFrame,
  unchanged, if `df` was empty.
- **Notes:** Numeric columns are rounded to whole numbers *for display
  only*; this does not modify the underlying data or any value used for
  further calculation elsewhere. Missing values (`NaN`) render as blank
  cells rather than the literal text "NaN".

#### `class AdpComparisonView` (`presentation/adp_comparison_view.py`)

- **Purpose:** Filters `AdpComparisonService`'s output by position and
  reshapes it into the exact columns/labels the ADP Comparison page
  displays. Pure display shaping — no data-fetching or identity-resolution
  logic.
- **Class constant:** `COLUMN_LABELS` — maps internal column names to their
  display labels: `player → "Player"`, `position → "Position"`,
  `espn_adp → "ESPN ADP"`, `yahoo_adp → "Yahoo ADP"`,
  `sleeper_adp → "Sleeper ADP"`.
- **Methods:**
  - **`shape(cls, df: pandas.DataFrame, position: str = "All") -> pandas.DataFrame`**
    *(class method)*
    - **Purpose:** Filters to one position (or keeps everyone, if `"All"`)
      and renames columns for display.
    - **Parameters:** `df` (`pandas.DataFrame`) —
      `AdpComparisonService.compare()`'s output; `position` (`str`) — a
      `Position` value or `"All"`.
    - **Returns:** `pandas.DataFrame` with columns `headshot`, `Player`,
      `Position`, `ESPN ADP`, `Yahoo ADP`, `Sleeper ADP`, ready for
      `presentation.table_style.style_table()`.
    - **Notes:** The headshot column holds a raw HTML `<img>` string per
      cell (e.g. `'<img src="..." height="40">'`), not a `shiny.ui.img()`
      tag object. An earlier version used `ui.img()` tags for Shiny's
      interactive `DataGrid`, which caused a client-side (browser
      JavaScript) serialization error requiring an active Shiny session to
      resolve HTML dependencies — that's a `DataGrid`-specific requirement.
      The table now renders through `style_table()`/pandas `Styler`
      (the same static-HTML-table approach as Player Profile's game log)
      instead of the interactive grid; a `Styler` renders raw HTML strings
      in cells directly, with no session dependency, so the plain-string
      approach works here where the tag-object approach didn't. The
      tradeoff: this table no longer has `DataGrid`'s built-in
      column-header sorting.

---

### `app_context.py`

#### `class AppContext`

- **Purpose:** The application's composition root — the single place where
  every repository, adapter, and service is constructed, in the correct
  dependency order, exactly once. Every Shiny panel receives this one
  object instead of constructing or receiving its own separate pieces.
- **Constructor:** `__init__(self, seasons: list)`
  - `seasons` (`list[int]`) — which NFL seasons `NflReadRepo` should load
    (e.g. `[2020, 2021, 2022, 2023, 2024, 2025]`).
- **Public attributes** (all built during construction, in this order):
  - `nfl_read_repo` (`NflReadRepo`)
  - `player_directory` (`PlayerDirectory`) — built from `nfl_read_repo`
  - `identity_repo` (`PlayerIdentityRepo`) — built from a new `CollectionRepo`
    pointed at `Collections.PLAYER_ID_MAP`, plus `player_directory` (for the
    exact-name-match fallback)
  - `udk_rankings_adapter` (`UdkRankingsAdapter`) — built from four new
    `CollectionRepo` instances, pointed at `Collections.UDK_QB_RANKINGS`,
    `UDK_RB_RANKINGS`, `UDK_WR_RANKINGS`, `UDK_TE_RANKINGS`
  - `roster_service` (`RosterService`) — built from `udk_rankings_adapter`,
    `identity_repo`, `player_directory`. Built early since several other
    pieces depend on it.
  - `espn_adp_adapter`, `sleeper_adp_adapter`, `yahoo_adp_adapter`
    (`EspnAdpAdapter`, `SleeperAdpAdapter`, `YahooAdpAdapter`) — built from
    `CollectionRepo`s pointed at `Collections.ESPN_PROJECTIONS`,
    `SLEEPER_PROJECTIONS`, `YAHOO_DRAFTANALYSIS` respectively
  - `adp_comparison_service` (`AdpComparisonService`) — built from the three
    ADP adapters, `identity_repo`, `roster_service`
  - `ffb_adapter` (`FfbProjectionsAdapter`) — built from two new
    `CollectionRepo` instances, pointed at `Collections.FFB_QB_PROJECTIONS`
    and `Collections.FFB_FLEX_PROJECTIONS`
  - `projections_service` (`ProjectionsService`) — built from `ffb_adapter`,
    `identity_repo`, `roster_service`
- **Notes:** `AppContext` is constructed exactly once, in `app.py`, and
  passed by reference into every panel's server function. Panels never
  construct their own repositories, adapters, or services — they only ever
  read from the attributes listed above.

---

### `panels/player_profile.py`

This file is a Shiny "module" — a self-contained, reusable pairing of a UI
function and a server function, namespaced by the `id` it's given when
included in the app (`"player_profile"`, set in `app.py`).

#### `player_profile_ui(ctx)` *(decorated `@module.ui`)*

- **Purpose:** Builds the visual layout of the Player Profile page: a
  player-selection dropdown and headshot on the left, a box score table on
  the right.
- **Parameters:** `ctx` (`AppContext`) — used once, at page-build time, to
  populate the dropdown's list of selectable players via
  `ctx.roster_service.player_names()` — scoped to the app's UDK-ranked
  roster, not every player nflreadpy knows about.
- **Returns:** a Shiny UI element (the result of `ui.layout_columns(...)`)
  to be embedded in the app's navigation.

#### `player_profile_server(input, output, session, ctx)` *(decorated `@module.server`)*

- **Purpose:** Defines the page's reactive behavior — what recalculates and
  redraws whenever the user picks a different player.
- **Parameters:** `input`, `output`, `session` — supplied automatically by
  Shiny for every module server function; `ctx` (`AppContext`) — provides
  access to `player_directory` for every data lookup this page needs.
- **Internal reactive functions:**
  - **`pp_player_pic()`** *(decorated `@render.ui`)*
    - **Purpose:** Renders the headshot image for whichever player is
      currently selected in the dropdown.
    - **Returns:** a Shiny UI `div` containing an `<img>` tag. If the
      looked-up image URL fails to load in the browser, an `onerror`
      handler swaps in the local default image as a second layer of
      fallback (in addition to `PlayerDirectory.get_headshot()`'s own
      server-side fallback).
  - **`player_gamelog_data()`** *(decorated `@reactive.calc`)*
    - **Purpose:** Computes the display-ready game log for the selected
      player. Shiny caches this calculation and only re-runs it when the
      selected player actually changes, avoiding unnecessary recomputation.
    - **Returns:** `pandas.DataFrame` — the result of passing
      `ctx.player_directory.get_gamelog(canonical_id)` through
      `GameLogView.shape()`.
  - **`box_score_table()`** *(decorated `@render.table`)*
    - **Purpose:** Renders the final, styled box score table shown on the
      page.
    - **Returns:** the styled table (via `style_table()`), or `None` if the
      selected player has no game log data — Shiny displays an empty table
      area in that case rather than an error.

---

### `panels/adp_comparison.py`

Another Shiny module, namespaced `"adp_comparison"` in `app.py`.

#### `adp_comparison_ui(ctx)` *(decorated `@module.ui`)*

- **Purpose:** Builds the ADP Comparison page: a position filter dropdown, a
  Half-PPR/Full-PPR scoring-format dropdown, a name-search box, and the
  table itself.
- **Parameters:** `ctx` (`AppContext`) — accepted for consistency with every
  other panel's signature, but not queried directly here (the position and
  format choices are fixed lists, not data pulled from `ctx`).
- **Returns:** a Shiny UI element. Uses a centered flexbox layout for the
  three controls (rather than Shiny's grid-based `layout_columns`) so they
  sit visually consolidated directly above the table.

#### `adp_comparison_server(input, output, session, ctx)` *(decorated `@module.server`)*

- **Purpose:** Wires the page's reactive behavior across three layered
  computations, each re-running only when it actually needs to.
- **Internal reactive functions:**
  - **`comparison_data()`** *(decorated `@reactive.calc`)*
    - **Purpose:** The expensive step — calls
      `ctx.adp_comparison_service.compare(fmt)`. Only re-runs when the
      scoring-format dropdown changes (that's the only input it reads).
    - **Returns:** `pandas.DataFrame`, `AdpComparisonService.compare()`'s
      output.
  - **`display_data()`** *(decorated `@reactive.calc`)*
    - **Purpose:** The cheap filtering step — applies the position filter
      via `AdpComparisonView.shape()`. Re-runs when the position dropdown
      changes, without re-running `comparison_data()`.
    - **Returns:** `pandas.DataFrame`, ready for display.
  - **`filtered_data()`** *(decorated `@reactive.calc`)*
    - **Purpose:** Applies the name-search box on top of `display_data()`'s
      output — a case-insensitive substring match against the `Player`
      column. Returns the data unfiltered if the search box is empty.
    - **Returns:** `pandas.DataFrame`.
  - **`adp_table()`** *(decorated `@render.table`)*
    - **Purpose:** Renders the final table.
    - **Returns:** `style_table(filtered_data())` — the same styled,
      static-HTML-table approach used by Player Profile's game log (see the
      `AdpComparisonView` reference entry above for why this isn't the
      interactive `DataGrid`).
- **Notes:** Three reactive layers, not two, is deliberate: scoring format
  (expensive) → position filter (cheap) → name search (cheap) — each
  control only triggers the work that actually depends on it.
# Player Universe Restructuring — What Changed and Why

## a) Overview

Previously, the app had no single, defined answer to "which players does
this app actually care about." `PlayerDirectory` was built entirely on
`nflreadpy`'s player-stats data, which covers essentially every NFL player
who's ever recorded a stat — tens of thousands of names across many seasons,
almost all irrelevant to a current fantasy draft. Player Profile's dropdown
showed all of them; nothing scoped ADP Comparison or projections to a
sensible player pool either.

This change introduces a single, explicit **player universe**: only players
ranked in UDK's (Ultimate Draft Kit) QB/RB/WR/TE rankings are in scope for
the app. Everything that shows a player list — Player Profile's dropdown,
ADP Platform Comparison, and now `ProjectionsService` — filters down to this
one roster instead of drawing from a much broader, less relevant pool.

`canonical_id` (the stable player-identity scheme used throughout the app)
is unchanged — it's still nflreadpy's internal player ID. What changed is
*which* players get surfaced, and — critically — *how identity gets
resolved for rookies*, since UDK ranks incoming rookies aggressively before
the season starts, and a design gap around rookie coverage surfaced during
this work (see below).

## b) Concise summary of changes

1. `NflReadRepo` gained a second data pull (`players()`), covering every
   rostered player regardless of games played — fixes rookie coverage.
2. `PlayerDirectory`'s identity/lookup methods switched to this broader
   source; game logs are unaffected.
3. New `UdkRankingsAdapter` reads UDK's four position-ranking collections.
4. `PlayerIdentityRepo` gained shared "resolve with fallback" methods, used
   by every service that needs to match external names to `canonical_id`.
5. New `RosterService` — the actual player-universe definition.
6. `AdpComparisonService` restructured from a 3-way outer join to a roster-
   driven left join.
7. `ProjectionsService` retrofitted with identity resolution and roster
   scoping (it had neither before).
8. `panels/player_profile.py`'s dropdown now sources from the roster.
9. `app_context.py` rewired to build and connect all of the above in the
   correct dependency order.

## c) Section-by-section detail

### 1. `NflReadRepo` — a second, broader data pull

**Goal:** `nflreadpy.load_player_stats()` (the app's only player data
source until now) has one row per player *per game played*. A rookie with
zero NFL games simply doesn't exist in that table — not a name-mismatch
problem, just literally absent. Since UDK ranks rookies prominently in
pre-draft rankings, this was a real gap: a meaningful share of UDK's top
rookie picks would have had no `canonical_id` at all.

**File:** `repositories/nfl_read_repo.py`

```python
def players(self):
    """
    Purpose: Returns a broad player reference (~25,000 rows) covering
        every player who's ever had an NFL roster spot, regardless of
        whether they've played a game yet. This is what makes rookies
        with zero recorded games resolvable -- player_stats() alone has
        no record of them at all.

    Returns:
        pd.DataFrame with columns including gsis_id (the same stable ID
        as player_stats()'s player_id -- confirmed identical format/values
        for the same player), display_name, position, headshot,
        latest_team, status.

    Notes:
        Loaded once, lazily, and cached -- same pattern as player_stats().
        Independent of `self.seasons`.
    """
    if self._players is None:
        self._players = nfl.load_players().to_pandas()
    return self._players
```

**Verification done:** confirmed `gsis_id` values in `players()` are unique
(25,033 rows, 25,033 unique IDs) and match `player_stats()`'s `player_id`
exactly for a known player (Chris Olave: `00-0037239` in both tables).

### 2. `PlayerDirectory` — switched to the broader source

**Goal:** identity/lookup methods (name, headshot, position, team) needed
to read from the new `players()` table instead of `player_stats()`, so
rookies are covered. Game logs are unaffected — they inherently need
per-game rows, which only `player_stats()` has.

**File:** `repositories/player_directory.py`

- `search_names()`, `get_display_name()`, `get_headshot()`, `get_position()`,
  `get_team()`, and `resolve_by_display_name()` now query
  `self._nfl_read_repo.players()` instead of `.player_stats()`. Column
  names changed accordingly: `player_id → gsis_id`,
  `player_display_name → display_name`, `headshot_url → headshot`,
  `team → latest_team` (all internal — the public method names/return
  shapes are unchanged).
- `get_gamelog()` is untouched, still reads `player_stats()`.
- Also fixed a pre-existing latent bug in `resolve_by_display_name()`
  (`.str.strip.str.upper()` — missing parens on `.strip`) while touching
  this method. It hadn't been hit yet since nothing called it with a
  `positions` argument until this change.

### 3. `UdkRankingsAdapter` — the definitive player list (new)

**Goal:** UDK's four position-ranking collections (`udk_qb_rankings_ppr`,
`udk_rb_rankings_ppr`, `udk_wr_rankings_ppr`, `udk_te_rankings_ppr`) all
share an identical schema, so this adapter is a straightforward
rename-and-concatenate — no messy duplicate-header handling like
`FfbProjectionsAdapter` needed.

**File:** `adapters/udk_rankings_adapter.py`

```python
class UdkRankingsAdapter:
    """
    Purpose: Reads UDK's four position-ranking collections and combines
        them into one canonical DataFrame -- the source of truth for
        "which players does the app consider in scope."
    """

    def load(self) -> pd.DataFrame:
        """
        Purpose: Reads and concatenates all four UDK ranking collections.
        Returns:
            pd.DataFrame with columns: name, position, team, bye_week,
            rank, points, risk, upside, adp, tier. No identity resolution
            applied yet -- that happens in RosterService.
        """
        ...
```

Note: `Outlook`/`Dynasty` (long-form text) and `Markers` (junk UI leftover
text, per `data/README.md`) are deliberately left out of the canonical
output — not needed for the roster/identity purpose this adapter serves
today. Easy to add later if a page wants to display that text.

### 4. `PlayerIdentityRepo` — shared "resolve with fallback" logic (new methods)

**Goal:** `AdpComparisonService` already had a private `_resolve()` method
doing "try `player_id_map`, then fall back to exact-name match." Once
`RosterService` needed the *same* two-step logic for UDK names, and
`ProjectionsService` needed it for FFB names, that logic needed to live in
exactly one shared place rather than being copy-pasted a third time.

**File:** `repositories/player_identity_repo.py`

```python
def resolve_many_with_fallback(self, source: str, names: pd.Series, positions: pd.Series = None) -> pd.Series:
    """
    Purpose: resolve_many() (the manually-curated player_id_map), falling
        back to PlayerDirectory.resolve_by_display_name() (an exact, not
        fuzzy, name/position match) for anything still unresolved. Shared
        by every service that needs to match a source's player names to
        canonical_id, so this two-step resolution logic lives in exactly
        one place.

    Parameters:
        source (str): which platform/source these names came from
            (e.g. "espn", "udk", "ffb").
        names (pd.Series of str): names to resolve.
        positions (pd.Series of str, optional): passed through to the
            fallback to reduce same-name collision risk.

    Returns:
        pd.Series, same shape as resolve_many()'s -- canonical_id or NaN
        per row.
    """
    mapped = self.resolve_many(source, names)
    still_missing = mapped.isna()
    if still_missing.any():
        mapped = mapped.copy()
        fallback_positions = positions[still_missing] if positions is not None else None
        mapped.loc[still_missing] = self._player_directory.resolve_by_display_name(
            names[still_missing], fallback_positions
        )
    return mapped
```

`unresolved_with_fallback()` mirrors this for the "which names need a
manual `player_id_map` row" diagnostic.

**Constructor change:** `PlayerIdentityRepo.__init__` now also takes
`player_directory`, since it needs it for the fallback. This rippled into
`AppContext` (the only place that constructs it) — a small, contained
change.

`AdpComparisonService._resolve()` was deleted; it now just calls
`self._identity_repo.resolve_many_with_fallback(...)` directly.

### 5. `RosterService` — the player universe itself (new)

**Goal:** the actual definition of "who's in the app." Combines
`UdkRankingsAdapter`'s raw rankings with identity resolution to produce a
roster keyed by `canonical_id`.

**File:** `services/roster_service.py`

```python
class RosterService:
    """
    Purpose: Defines the app's player universe. Only players ranked in
        UDK's QB/RB/WR/TE rankings are "in scope" for this app. Everything
        else that needs a player list filters down to this roster rather
        than showing every player nflreadpy has ever heard of.
    """

    def roster(self) -> pd.DataFrame:
        """
        Purpose: The full in-scope player roster.
        Returns:
            pd.DataFrame with columns: canonical_id, display_name,
            headshot_url, position, team, bye_week, rank, points, risk,
            upside, adp, tier. UDK players who can't be resolved to a
            canonical_id at all are excluded (see unresolved()).
        Notes:
            If a player is mistakenly ranked in more than one position
            file, only the best (lowest) rank is kept.
        """
        ...

    def canonical_ids(self) -> set: ...   # bare set of in-scope IDs
    def player_names(self) -> dict: ...   # {canonical_id: display_name}, dropdown-ready
    def unresolved(self) -> list: ...     # UDK names needing manual player_id_map rows
```

**Why K/DST are entirely out of scope:** UDK only ranks QB/RB/WR/TE — there
are no `udk_k_rankings_ppr`/`udk_dst_rankings_ppr` files. This is a direct,
intentional consequence of using UDK rankings as the universe definition,
consistent with how the rest of the app (e.g. `ProjectionsService`) already
didn't handle K/DST.

### 6. `AdpComparisonService` — restructured to be roster-driven

**Goal:** previously, `compare()` did a 3-way outer join across whichever
players ESPN/Yahoo/Sleeper happened to mention. Now it starts *from the
roster* and left-joins each platform's ADP onto it.

**File:** `services/adp_comparison_service.py`

Before: `espn.merge(sleeper, how="outer").merge(yahoo, how="outer")`, then
drop rows blank on all three.

After:

```python
def compare(self, fmt: ScoringFormat) -> pd.DataFrame:
    """
    ...
    Notes:
        Uses a left join from the roster, so every UDK-ranked player
        shows even if 0 of the 3 platforms have them (blank ADP in those
        columns); a platform player who ISN'T UDK-ranked never enters
        the result at all.
    """
    base = self._roster_service.roster()[["canonical_id", "display_name", "headshot_url", "position"]]

    espn = self._prepare("espn", self._espn.load(fmt), "espn_adp")
    sleeper = self._prepare("sleeper", self._sleeper.load(fmt), "sleeper_adp")
    yahoo = self._prepare("yahoo", self._yahoo.load(fmt), "yahoo_adp")

    result = base.merge(espn, on="canonical_id", how="left")
    result = result.merge(sleeper, on="canonical_id", how="left")
    result = result.merge(yahoo, on="canonical_id", how="left")

    return result[["canonical_id", "display_name", "headshot_url", "position",
                    "espn_adp", "yahoo_adp", "sleeper_adp"]]
```

This also let `_profiles_for()` be deleted entirely — `display_name`,
`headshot_url`, and `position` now come straight from the roster, since the
roster already carries them.

### 7. `ProjectionsService` — retrofitted with identity + roster scoping

**Goal:** `ProjectionsService` never had `canonical_id` resolution at all
before this change — it just returned FFB's raw projected stats + computed
fantasy points, with no way to know which real player each row was, or to
filter down to the roster. This was flagged as a known gap when the service
was first built; this change closes it.

**File:** `services/projections_service.py`

```python
def get_own_projections(self) -> pd.DataFrame:
    """
    ...
    Returns:
        ...plus canonical_id. Only players resolved to a canonical_id
        AND present in RosterService's UDK roster are included.
    """
    combined = self._ffb_adapter.load()

    canonical_id = self._identity_repo.resolve_many_with_fallback(
        "ffb", combined["name"], combined["position"]
    )
    combined = combined.assign(canonical_id=canonical_id).dropna(subset=["canonical_id"])

    roster_ids = self._roster_service.canonical_ids()
    combined = combined[combined["canonical_id"].isin(roster_ids)]

    # ...scoring math unchanged from here...
```

Also added `ProjectionsService.unresolved()`, mirroring the same diagnostic
pattern used elsewhere.

### 8. `panels/player_profile.py` — dropdown now roster-scoped

One-line change: `choices=ctx.player_directory.search_names()` became
`choices=ctx.roster_service.player_names()`. The default selection (Chris
Olave, `00-0037239`) was verified to still be present in the roster, so no
further change was needed there.

### 9. `app_context.py` — wiring

Build order matters here: `player_directory` must exist before
`identity_repo` (which now depends on it); `identity_repo` and
`player_directory` must both exist before `roster_service`; and
`roster_service` must exist before `adp_comparison_service` and
`projections_service` (both now depend on it).

## Verification performed

All checked against real, live data (not synthetic):

| Check | Result |
|---|---|
| `RosterService.roster()` size | 296 players (of ~303 UDK-ranked; QB/RB/WR/TE only) |
| `RosterService.unresolved()` | 11 names — almost all Jr./Sr./III suffix mismatches, a good candidate list for manual `player_id_map` entries |
| `AdpComparisonService.compare()` row count | 296 (matches roster size exactly, confirming the left-join logic) |
| Rows with all 3 platforms blank | 3 (a small, expected number of UDK-ranked players absent from all three ADP platforms) |
| `ProjectionsService.get_own_projections()` row count | 285 (a subset of the 296-player roster, since FFB's own projection file doesn't cover every UDK-ranked player 1:1) |
| Chris Olave in roster | Confirmed present (Player Profile's default selection still valid) |
| Full app boot | Starts cleanly, both Player Profile and ADP Comparison pages serve with no errors |

## Follow-ups / things to know going forward

- **11 UDK players still unresolved.** Run `ctx.roster_service.unresolved()`
  to see the current list, and add rows to `data/player_id_map.csv` (source
  `"udk"`) for any that should resolve. The same applies to
  `ctx.adp_comparison_service.unresolved(source)` for ESPN/Yahoo/Sleeper and
  `ctx.projections_service.unresolved()` for FFB.
- **K and DST are entirely out of scope**, not just deprioritized — there's
  no UDK ranking file for either position. If DST/K support is ever wanted,
  it would need its own separate universe-definition path, since it can't
  ride on `RosterService` as designed.
- **Startup is now slower** than before this change, since `NflReadRepo`
  loads two separate `nflreadpy` datasets (`player_stats()` *and*
  `players()`) instead of one. Both are still lazy/cached, so this only
  affects first-use latency, not repeated calls.
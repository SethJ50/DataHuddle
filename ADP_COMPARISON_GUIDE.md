# ADP Platform Comparison

## a) Overview of the feature

A new page, **ADP Platform Comparison**, showing one table with columns
**[headshot, player, ESPN ADP, Yahoo ADP, Sleeper ADP]** — filterable by
position (or "All"), sortable by clicking any column header, and searchable.
It builds on the existing backend architecture described in
`ARCHITECTURE.md` (`repositories/`, `adapters/`, `services/`,
`presentation/`, `AppContext`).

**Key decisions this design is built on:**
- ADP is read directly from the three collections that already exist today
  (`espn_projections`, `sleeper_projections`, `yahoo_draftanalysis`) — no new
  data ingestion needed.
- ESPN/Sleeper's Half-PPR vs. Full-PPR ADP is a toggle on the page itself;
  Yahoo only has one ADP value (standard scoring), unaffected by the toggle.
- `player_id_map` (the manually-curated cross-source player mapping table)
  has zero rows today. Rather than blocking on manual curation, identity
  resolution tries `player_id_map` first, then falls back to an exact
  (not fuzzy) case/whitespace-normalized name match against the nflreadpy
  player directory — so the page works immediately for most players, and
  `player_id_map` becomes the mechanism for patching the specific names that
  don't match exactly.
- DST (team defenses) is out of scope for v1 — `nflreadpy` has no individual
  defense-player rows to resolve against, consistent with how the rest of
  the app already treats DST.
- `shiny==1.5.0`'s `render.DataGrid`/`DataTable` gives sorting, per-column
  filtering, and even image cells natively — confirmed directly against the
  installed package, no extra libraries needed.

## b) Concise TODO list

1. Add `resolve_by_display_name()` to `PlayerDirectory` (identity fallback).
2. Build `adapters/adp_source_adapter.py` (ESPN/Sleeper/Yahoo adapters).
3. Build `services/adp_comparison_service.py` (joins all three sources).
4. Build `presentation/adp_comparison_view.py` (shapes data for the grid).
5. Wire the new pieces into `AppContext`.
6. Build `panels/adp_comparison.py` (the actual page).
7. Verify: unit-style sanity check, then run the live app, then spot-check
   how many player names still need manual `player_id_map` entries.

Build in this exact order — each step depends on the one before it existing.

## c) Section-by-section detail

Each section below has extra **"Concept check"** boxes wherever the code
leans on something that isn't just basic Python syntax (pandas vectorized
operations, `Protocol`s, Shiny's reactive model, or MongoDB access
patterns) — those are the parts most likely to feel unfamiliar coming into
this codebase.

---

### 1. Teach `PlayerDirectory` to look players up by name

**Goal:** Given a player's printed name (as ESPN/Yahoo/Sleeper spell it),
find their stable internal ID by exact-matching it against the nflreadpy
player directory — the fallback used when `player_id_map` doesn't have a
manual entry for that name yet.

**File:** `repositories/player_directory.py` (existing file — add this
method inside the `PlayerDirectory` class; don't touch what's already there)

```python
def resolve_by_display_name(self, names: pd.Series, positions: pd.Series = None) -> pd.Series:
    """
    Purpose: Looks up canonical player IDs by exact-matching printed player
        names (and optionally position) against the nflreadpy player
        directory. This is the fallback used when a player has no manual
        entry yet in player_id_map (see PlayerIdentityRepo).

    Parameters:
        names (pd.Series of str): source-specific player names to resolve,
            e.g. the "name" column pulled from an ESPN/Yahoo/Sleeper
            DataFrame.
        positions (pd.Series of str, optional): one position per name, used
            alongside the name to reduce false matches when two different
            players happen to share a name. Defaults to None (name-only
            match).

    Returns:
        pd.Series: same length/index as `names`, with each entry replaced by
            its matching canonical_id, or NaN where no exact match was found.

    Notes:
        This is an EXACT match only (case/whitespace normalized) — not
        fuzzy or similarity-based. A name spelled even slightly differently
        across sources (e.g. a missing suffix) will not match here and
        needs a manual player_id_map row instead.
    """
    # Pull just the columns we need from the full stats table, and keep only
    # one row per player (the full table has one row per player PER GAME).
    df = (
        self._stats()[["player_id", "player_display_name", "position"]]
        .dropna(subset=["player_display_name"])
        .drop_duplicates(subset="player_id")
    )

    # Normalize casing/whitespace on both sides, so "Chris Olave" and
    # " chris olave " are treated as the same name.
    df["_key"] = df["player_display_name"].str.strip().str.lower()
    keys = names.str.strip().str.lower()

    if positions is not None:
        # Glue the position onto the name (e.g. "chris olave|WR") to reduce
        # the odds of two same-named players being confused for each other.
        df["_key"] = df["_key"] + "|" + df["position"].str.strip().str.upper()
        keys = keys + "|" + positions.str.strip().str.upper()

    # Build a name -> canonical_id lookup table, then look up every
    # incoming name against it in one vectorized call (much faster than a
    # Python for-loop over each name).
    lookup = df.drop_duplicates(subset="_key").set_index("_key")["player_id"]
    return keys.map(lookup)
```

You'll also need `import pandas as pd` at the top of this file if it isn't
already there (check first).

> **Concept check — vectorized string operations**
> `.str.strip().str.lower()` applies `strip()`/`lower()` to *every value in
> the column at once* — this is the idiomatic pandas way to do a bulk text
> transformation, instead of writing a loop that processes one name at a
> time. `keys.map(lookup)` is the same idea for lookups: it checks every
> value in `keys` against the `lookup` table in one call.

---

### 2. Build the three ADP adapters

**Goal:** Each data source (ESPN, Sleeper, Yahoo) names its ADP column
differently. These adapters translate each one into an identical shape —
`name`, `team`, `position`, `adp` — so nothing downstream needs to know
which platform a row came from.

**File:** `adapters/adp_source_adapter.py`

```python
"""
Adapters that convert each ADP data source's own column layout into one
common ("canonical") shape, so services/adp_comparison_service.py can treat
ESPN, Sleeper, and Yahoo identically regardless of their underlying quirks.
"""

from typing import Protocol

import pandas as pd

from scoring import ScoringFormat


class AdpSourceAdapter(Protocol):
    """
    Purpose: Documents the shape every ADP adapter must have. Not meant to
        be inherited from directly — see the "Concept check" note below on
        what a Protocol actually is in Python.
    """

    def load(self, fmt: ScoringFormat) -> pd.DataFrame:
        """
        Purpose: Loads this platform's ADP data.
        Parameters:
            fmt (ScoringFormat): which scoring format's ADP to use (only
                matters for platforms with a half-PPR/full-PPR split).
        Returns:
            pd.DataFrame with columns: name, team, position, adp.
        """
        ...


def _load_projection_adp(collection_repo, fmt: ScoringFormat) -> pd.DataFrame:
    """
    Purpose: Shared helper for EspnAdpAdapter/SleeperAdpAdapter, since both
        of their source collections already carry half_ppr_adp/full_ppr_adp
        columns side by side — this avoids writing the same column-picking
        logic twice.

    Parameters:
        collection_repo (CollectionRepo): already pointed at the source
            collection (e.g. espn_projections).
        fmt (ScoringFormat): HALF_PPR or FULL_PPR — picks which ADP column
            to read.

    Returns:
        pd.DataFrame with columns: name, team, position, adp.
    """
    df = collection_repo.read()
    # Pick the right column name based on which scoring format was asked for.
    adp_col = "half_ppr_adp" if fmt == ScoringFormat.HALF_PPR else "full_ppr_adp"

    return pd.DataFrame({
        "name": df["name"],
        "team": df["team"],
        "position": df["position"],
        "adp": df[adp_col],
    })


class EspnAdpAdapter:
    """
    Purpose: Exposes ESPN's projection data as ADP in the app's common
        [name, team, position, adp] shape.
    """

    def __init__(self, collection_repo):
        """
        Parameters:
            collection_repo (CollectionRepo): should already be pointed at
                Collections.ESPN_PROJECTIONS.
        """
        self._collection_repo = collection_repo

    def load(self, fmt: ScoringFormat) -> pd.DataFrame:
        """
        Purpose: Loads ESPN's ADP for the requested scoring format.
        Parameters: fmt (ScoringFormat) — HALF_PPR or FULL_PPR.
        Returns: pd.DataFrame with columns name, team, position, adp.
        """
        return _load_projection_adp(self._collection_repo, fmt)


class SleeperAdpAdapter:
    """
    Purpose: Exposes Sleeper's projection data as ADP in the app's common
        [name, team, position, adp] shape.
    """

    def __init__(self, collection_repo):
        """
        Parameters:
            collection_repo (CollectionRepo): should already be pointed at
                Collections.SLEEPER_PROJECTIONS.
        """
        self._collection_repo = collection_repo

    def load(self, fmt: ScoringFormat) -> pd.DataFrame:
        """
        Purpose: Loads Sleeper's ADP for the requested scoring format.
        Parameters: fmt (ScoringFormat) — HALF_PPR or FULL_PPR.
        Returns: pd.DataFrame with columns name, team, position, adp.
        Notes: Sleeper tracks genuinely different ADP per format, unlike
            ESPN (whose half/full-PPR ADP columns hold the same value).
        """
        return _load_projection_adp(self._collection_repo, fmt)


class YahooAdpAdapter:
    """
    Purpose: Exposes Yahoo's draft analysis data as ADP in the app's common
        [name, team, position, adp] shape. Yahoo only publishes one ADP
        value (standard scoring), so the scoring-format toggle has no
        effect on this platform's numbers.
    """

    def __init__(self, collection_repo):
        """
        Parameters:
            collection_repo (CollectionRepo): should already be pointed at
                Collections.YAHOO_DRAFTANALYSIS.
        """
        self._collection_repo = collection_repo

    def load(self, fmt: ScoringFormat) -> pd.DataFrame:
        """
        Purpose: Loads Yahoo's ADP. `fmt` is accepted only so this class
            matches the same shape as the other two adapters — Yahoo has no
            per-format ADP split, so the value returned is the same either
            way.
        Parameters: fmt (ScoringFormat) — accepted but unused.
        Returns: pd.DataFrame with columns name, team, position, adp.
        """
        df = self._collection_repo.read()

        return pd.DataFrame({
            "name": df["name"],
            "team": df["team"],
            "position": df["position"],
            "adp": df["adp"],
        })
```

> **Concept check — what is `Protocol`?**
> `AdpSourceAdapter` is never actually used as a parent class — none of the
> three real adapters above write `class EspnAdpAdapter(AdpSourceAdapter)`.
> That's intentional. A `Protocol` in Python is just a *documented shape*:
> it says "anything with a `load(fmt)` method that returns a DataFrame with
> these columns counts as an `AdpSourceAdapter`." Python doesn't check this
> at runtime — it's there purely so a human reading the code can see what's
> expected. You can ignore it if it's confusing; the three real classes
> above don't need it to work.
>
> **Concept check — why does `collection_repo` show up in every constructor?**
> This is the same `CollectionRepo` class already in
> `repositories/collection_repo.py`. It wraps one MongoDB collection and
> remembers ("caches") the result after the first read, so calling
> `.read()` many times doesn't hit the database every time. These adapters
> don't talk to MongoDB directly at all — they're handed an already-built
> `CollectionRepo` (that wiring happens in Step 5) and just call `.read()`
> on it.
>
> **Concept check — Yahoo's dual-eligibility positions**
> Yahoo's `position` field can be a string like `"RB,TE"` for dual-eligible
> players, passed through here as-is. That matters one layer later: the
> identity fallback in Step 3 matches on `(name, position)`, so a Yahoo row
> with `"RB,TE"` won't match `PlayerDirectory`'s single-valued `"RB"` or
> `"TE"` — a few of these players may show up as unresolved and need a
> manual `player_id_map` row. That's expected, not a bug.

---

### 3. Build the comparison service

**Goal:** Combine all three platforms' ADP into one table, resolving each
platform's player names to the app's stable canonical ID so headshots and
positions can be attached from `PlayerDirectory`.

**File:** `services/adp_comparison_service.py`

```python
"""
Combines ADP from ESPN, Sleeper, and Yahoo into one comparison table, keyed
by each player's stable canonical_id.
"""

import pandas as pd

from scoring import ScoringFormat


class AdpComparisonService:
    """
    Purpose: The one place "what is this player's ADP on each platform"
        gets answered. Composes the three ADP adapters, the identity
        resolution layer (PlayerIdentityRepo + PlayerDirectory), and
        PlayerDirectory's authoritative player info (name/headshot/position)
        into a single combined table.
    """

    def __init__(self, espn_adapter, sleeper_adapter, yahoo_adapter, identity_repo, player_directory):
        """
        Parameters:
            espn_adapter (EspnAdpAdapter)
            sleeper_adapter (SleeperAdpAdapter)
            yahoo_adapter (YahooAdpAdapter)
            identity_repo (PlayerIdentityRepo): the manually-curated
                player_id_map lookup, tried first.
            player_directory (PlayerDirectory): authoritative player
                info, and the exact-name-match fallback.
        """
        self._espn = espn_adapter
        self._sleeper = sleeper_adapter
        self._yahoo = yahoo_adapter
        self._identity_repo = identity_repo
        self._player_directory = player_directory

    def compare(self, fmt: ScoringFormat) -> pd.DataFrame:
        """
        Purpose: Builds the full ADP comparison table across all three
            platforms.

        Parameters:
            fmt (ScoringFormat): HALF_PPR or FULL_PPR — passed through to
                the ESPN/Sleeper adapters (Yahoo ignores it).

        Returns:
            pd.DataFrame with one row per resolved player, columns:
            canonical_id, display_name, headshot_url, position, espn_adp,
            yahoo_adp, sleeper_adp.

        Notes:
            Uses an outer join, so a player missing from one or two
            platforms still gets a row (with blank values in those
            columns) rather than being dropped. Players with NO ADP data
            on any platform are excluded.
        """
        espn = self._prepare("espn", self._espn.load(fmt), "espn_adp")
        sleeper = self._prepare("sleeper", self._sleeper.load(fmt), "sleeper_adp")
        yahoo = self._prepare("yahoo", self._yahoo.load(fmt), "yahoo_adp")

        # Outer join: keep every player found on ANY platform, even if
        # they're missing from the other two.
        merged = espn.merge(sleeper, on="canonical_id", how="outer")
        merged = merged.merge(yahoo, on="canonical_id", how="outer")

        # Drop rows where all three ADP values are blank -- not a useful
        # comparison row.
        merged = merged.dropna(subset=["espn_adp", "sleeper_adp", "yahoo_adp"], how="all")

        # Attach authoritative name/headshot/position from PlayerDirectory.
        profiles = self._profiles_for(merged["canonical_id"])
        result = merged.merge(profiles, on="canonical_id", how="left")

        return result[["canonical_id", "display_name", "headshot_url", "position",
                        "espn_adp", "yahoo_adp", "sleeper_adp"]]

    def unresolved(self, source: str) -> list:
        """
        Purpose: Reports which player names from one platform could not be
            matched to a canonical_id at all -- these are the names that
            need a manual player_id_map row.

        Parameters:
            source (str): "espn", "sleeper", or "yahoo".

        Returns:
            list[str]: unresolved names, de-duplicated.
        """
        adapter = {"espn": self._espn, "sleeper": self._sleeper, "yahoo": self._yahoo}[source]
        names = adapter.load(ScoringFormat.HALF_PPR)["name"]
        resolved = self._resolve(source, names)
        return names[resolved.isna()].drop_duplicates().tolist()

    def _resolve(self, source, names):
        """
        Purpose: Resolves a column of source-specific player names to
            canonical_ids, trying the manually-curated player_id_map first
            and falling back to an exact name match.

        Parameters:
            source (str): which platform these names came from.
            names (pd.Series of str): the names to resolve.

        Returns:
            pd.Series: same shape as `names`, canonical_id or NaN per row.

        Notes:
            Shared by both compare() and unresolved() so there's exactly
            one place this two-step resolution logic lives.
        """
        mapped = self._identity_repo.resolve_many(source, names)
        still_missing = mapped.isna()
        if still_missing.any():
            mapped = mapped.copy()
            mapped.loc[still_missing] = self._player_directory.resolve_by_display_name(
                names[still_missing]
            )
        return mapped

    def _prepare(self, source, df, adp_col):
        """
        Purpose: Resolves one platform's raw ADP rows to canonical_id, and
            collapses any accidental duplicate rows per player.

        Parameters:
            source (str): which platform this data came from.
            df (pd.DataFrame): that platform's raw [name, ..., adp] rows.
            adp_col (str): what to name the ADP column in the output
                (e.g. "espn_adp").

        Returns:
            pd.DataFrame with columns: canonical_id, <adp_col>. One row per
            resolved player; unresolved names are dropped (not shown blank).
        """
        canonical_id = self._resolve(source, df["name"])
        out = pd.DataFrame({"canonical_id": canonical_id, adp_col: df["adp"]})
        out = out.dropna(subset=["canonical_id"])
        # Safety net: if a source ever has two rows for the same resolved
        # player, keep the lower (better) ADP rather than erroring.
        return out.groupby("canonical_id", as_index=False)[adp_col].min()

    def _profiles_for(self, canonical_ids: pd.Series) -> pd.DataFrame:
        """
        Purpose: Looks up authoritative display name/headshot/position for
            a set of canonical_ids, once per unique player.

        Parameters:
            canonical_ids (pd.Series of str): may contain duplicates/blanks.

        Returns:
            pd.DataFrame with columns: canonical_id, display_name,
            headshot_url, position -- one row per unique canonical_id.
        """
        unique_ids = canonical_ids.dropna().drop_duplicates()
        rows = [{
            "canonical_id": cid,
            "display_name": self._player_directory.get_display_name(cid),
            "headshot_url": self._player_directory.get_headshot(cid),
            "position": self._player_directory.get_position(cid),
        } for cid in unique_ids]
        return pd.DataFrame(rows)
```

> **Concept check — `merge(..., how="outer")`**
> A pandas `merge` is the same idea as a SQL `JOIN` — it lines up two tables
> by a shared column (here, `canonical_id`) and combines their other
> columns into one row per matching ID. `how="outer"` means: keep *every*
> player from *both* sides, even if they only appear on one platform. (The
> alternative, `how="inner"`, would silently drop any player not present on
> all three — not what we want here.)
>
> **Concept check — `groupby("canonical_id").min()`**
> This exists purely as a safety net, in case one source somehow has two
> rows that resolve to the same player. `groupby` bundles rows sharing the
> same `canonical_id` into groups, and `.min()` collapses each group down
> to one row by taking the smallest ADP value. In the normal case, this is
> a no-op — it only matters if duplicates ever sneak in.
>
> **Concept check — `dropna(subset=[...], how="all")`**
> `subset=[...]` says "only look at these three columns." `how="all"` says
> "drop this row only if *every one* of those columns is blank." A player
> missing from just one or two platforms still shows up (with blanks in
> those columns) — only dropped if they have *no* ADP data anywhere.

---

### 4. Build the display-shaping layer

**Goal:** Take the service's combined table and shape it into exactly what
the on-screen grid should show: filtered by position, headshot URLs turned
into real images, columns renamed for display.

**File:** `presentation/adp_comparison_view.py`

```python
"""
Shapes AdpComparisonService's output for on-screen display. Contains no
data-fetching or identity-resolution logic -- purely column selection,
filtering, and formatting.
"""

import pandas as pd
from shiny import ui


class AdpComparisonView:
    """
    Purpose: Converts the ADP comparison service's raw output into the
        exact table shape the ADP Platform Comparison page displays.
    """

    COLUMN_LABELS = {
        "headshot": "",
        "player": "Player",
        "espn_adp": "ESPN ADP",
        "yahoo_adp": "Yahoo ADP",
        "sleeper_adp": "Sleeper ADP",
    }

    @classmethod
    def shape(cls, df: pd.DataFrame, position: str = "All") -> pd.DataFrame:
        """
        Purpose: Filters by position and reshapes the comparison data into
            display-ready columns, with headshots as real images.

        Parameters:
            df (pd.DataFrame): AdpComparisonService.compare()'s output.
            position (str): a Position value (e.g. "WR") to filter to, or
                "All" (default) to show every position.

        Returns:
            pd.DataFrame with columns [headshot, player, espn_adp, yahoo_adp,
            sleeper_adp], renamed to their display labels, ready to hand to
            shiny.render.DataGrid.
        """
        if df.empty:
            return pd.DataFrame(columns=list(cls.COLUMN_LABELS.values()))

        if position != "All":
            df = df[df["position"] == position]

        display = pd.DataFrame({
            # Turn each headshot URL into an actual image element instead
            # of a plain text string -- see the Concept check below.
            "headshot": df["headshot_url"].apply(lambda url: ui.img(src=url, height="40px")),
            "player": df["display_name"],
            "espn_adp": df["espn_adp"],
            "yahoo_adp": df["yahoo_adp"],
            "sleeper_adp": df["sleeper_adp"],
        })

        return display.rename(columns=cls.COLUMN_LABELS)
```

> **Concept check — putting `ui.img(...)` inside a DataFrame column**
> Normally a DataFrame column holds numbers or text. Here, `.apply(lambda
> url: ui.img(...))` replaces every URL in the `headshot_url` column with an
> actual Shiny image *object* instead of a plain string. Shiny's
> `DataGrid`/`DataTable` (used in Step 6) specifically knows how to detect
> these "tag" objects and renders them as real `<img>` elements in the
> browser. This was confirmed directly against the installed Shiny version
> (1.5.0) — it's a real, supported feature, not a workaround.

---

### 5. Wire the new pieces into `AppContext`

**Goal:** Construct one instance of each new adapter/service, in the
correct order, alongside everything else the app already builds once at
startup.

**File:** `app_context.py` (existing file — add to it, don't remove
anything)

At the top, add these imports alongside the existing ones:

```python
from adapters.adp_source_adapter import EspnAdpAdapter, SleeperAdpAdapter, YahooAdpAdapter
from services.adp_comparison_service import AdpComparisonService
```

Then, inside `AppContext.__init__`, *after* the existing
`self.identity_repo = ...` and after `self.player_directory` already exists
(both are needed as ingredients), add:

```python
# ADP Platform Comparison: one adapter per platform, reusing the existing
# projection/draft-analysis collections (no new data ingestion needed).
self.espn_adp_adapter = EspnAdpAdapter(CollectionRepo(Collections.ESPN_PROJECTIONS))
self.sleeper_adp_adapter = SleeperAdpAdapter(CollectionRepo(Collections.SLEEPER_PROJECTIONS))
self.yahoo_adp_adapter = YahooAdpAdapter(CollectionRepo(Collections.YAHOO_DRAFTANALYSIS))

self.adp_comparison_service = AdpComparisonService(
    self.espn_adp_adapter,
    self.sleeper_adp_adapter,
    self.yahoo_adp_adapter,
    self.identity_repo,
    self.player_directory,
)
```

> **Concept check — why does order matter here?**
> `AppContext` builds everything once, top to bottom, like following a
> recipe — later ingredients can use earlier ones, but not the other way
> around. `AdpComparisonService` needs `self.identity_repo` and
> `self.player_directory` to already exist as *finished objects* before it
> can be constructed, so this new block has to go after both of those
> lines, not before.

---

### 6. Build the actual page

**Goal:** The user-facing part — a position filter, a scoring-format
toggle, and the interactive, sortable/searchable table itself.

**File:** `panels/adp_comparison.py` (existing stub — replace its contents
entirely; it currently just has a placeholder card and an empty server)

```python
"""
The ADP Platform Comparison page: lets the user filter by position, toggle
Half-PPR/Full-PPR scoring, and browse a sortable/searchable table comparing
ADP across ESPN, Yahoo, and Sleeper.
"""

from shiny import module, ui, render, reactive

from scoring import ScoringFormat
from registry import Position
from presentation.adp_comparison_view import AdpComparisonView


@module.ui
def adp_comparison_ui(ctx):
    """
    Purpose: Builds the page layout -- a position filter dropdown, a
        scoring-format toggle, and the table itself.

    Parameters:
        ctx (AppContext): not queried directly here (unlike Player Profile's
            dropdown), since the position/format choices are fixed lists
            rather than data pulled from ctx. Still accepted so this
            function's signature matches every other panel's pattern.

    Returns:
        A Shiny UI element to embed in the app's navigation.
    """
    # Build {"All": "All", "QB": "QB", "RB": "RB", ...} for the dropdown.
    position_choices = {"All": "All"}
    for p in Position:
        position_choices[p.value] = p.value

    return ui.card(
        ui.layout_columns(
            ui.input_select("position", "Position", choices=position_choices, selected="All"),
            ui.input_radio_buttons(
                "scoring_format",
                "Scoring Format",
                choices={
                    ScoringFormat.HALF_PPR.value: "Half PPR",
                    ScoringFormat.FULL_PPR.value: "Full PPR",
                },
                selected=ScoringFormat.HALF_PPR.value,
                inline=True,
            ),
            col_widths=(3, 3),
        ),
        ui.output_data_frame("adp_table"),
        full_screen=True,
    )


@module.server
def adp_comparison_server(input, output, session, ctx):
    """
    Purpose: Wires the page's reactive behavior -- recomputing the
        comparison table when the scoring format changes, and re-filtering
        it when the position dropdown changes.

    Parameters:
        input, output, session: supplied automatically by Shiny.
        ctx (AppContext): provides ctx.adp_comparison_service.
    """

    @reactive.calc
    def comparison_data():
        """
        Purpose: Runs the expensive step -- loading all three platforms and
            resolving player identities -- only when the scoring-format
            toggle changes.
        Returns: pd.DataFrame, AdpComparisonService.compare()'s output.
        """
        fmt = ScoringFormat(input.scoring_format())
        return ctx.adp_comparison_service.compare(fmt)

    @reactive.calc
    def display_data():
        """
        Purpose: Runs the cheap step -- filtering by position and shaping
            for display -- every time the position dropdown changes,
            without redoing the expensive comparison_data() step.
        Returns: pd.DataFrame, ready for the grid.
        """
        return AdpComparisonView.shape(comparison_data(), input.position())

    @render.data_frame
    def adp_table():
        """
        Purpose: Renders the final interactive table.
        Returns: a shiny.render.DataGrid, which gives sorting (click any
            column header) and per-column filtering/search (filters=True)
            with no extra code.
        """
        return render.DataGrid(display_data(), filters=True, width="100%", height="700px")
```

> **Concept check — why two separate `@reactive.calc` functions?**
> Shiny automatically figures out which reactive functions need to re-run
> whenever an input changes, by tracking which inputs each function reads.
> `comparison_data()` reads `input.scoring_format()` — the *expensive* step
> — so it only re-runs when you flip the Half/Full-PPR toggle.
> `display_data()` reads `input.position()` — the *cheap* filtering step —
> so switching the position dropdown re-filters instantly without redoing
> the expensive work. Writing this as one single function instead would
> mean changing the position filter re-runs the entire expensive comparison
> every time, for no reason.
>
> **Concept check — `render.DataGrid(..., filters=True)`**
> This is what actually gives you sorting, filtering, and search, all for
> free: clicking any column header sorts by it automatically, and
> `filters=True` adds a small text box under every column header — typing
> in the one under "Player" gives you a live substring search. None of that
> needs to be hand-built.

---

## Verification — do these after all 6 steps are done

1. **Sanity-check without touching the live app or MongoDB first.** Open a
   Python shell in the project folder and try:
   ```python
   from repositories.collection_repo import CollectionRepo
   import pandas as pd
   repo = CollectionRepo("fake")
   repo._df = pd.DataFrame([{"name": "Test Player", "team": "XX", "position": "WR", "half_ppr_adp": 5.0, "full_ppr_adp": 4.0}])
   from adapters.adp_source_adapter import EspnAdpAdapter
   from scoring import ScoringFormat
   print(EspnAdpAdapter(repo).load(ScoringFormat.HALF_PPR))
   ```
   This bypasses MongoDB entirely (by directly setting `repo._df`) so you can
   confirm the adapter logic works before worrying about live data.

2. **Run the real app:**
   ```
   shiny run --reload --launch-browser app.py
   ```
   Open ADP Platform Comparison. Confirm: real headshots show up, the
   position dropdown filters the table, the Half/Full-PPR toggle changes
   ESPN/Sleeper's numbers (Yahoo's column should stay the same either way),
   clicking a column header sorts by it, and typing into the filter box
   under "Player" searches by name.

3. **Check how much manual work `player_id_map` curation will actually take.**
   In a Python shell, with a real `AppContext` built:
   ```python
   from app_context import AppContext
   ctx = AppContext([2024])
   print(ctx.adp_comparison_service.unresolved("yahoo")[:20])
   ```
   This shows the first 20 real player names that couldn't be matched by
   either the manual mapping table or the exact-name fallback — this is
   your real-world signal for whether the exact-match approach is "good
   enough" or whether a lot of manual `player_id_map` entries will be
   needed soon.
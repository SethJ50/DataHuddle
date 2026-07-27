# Player ID Mapping Guide

How to fix players who are missing or mismatched because a data source spells
their name differently than nflreadpy does (the "Travis Etienne" problem).

Keep this handy — the same fix applies every time a `Jr./Sr./III` suffix,
nickname, punctuation, or spelling difference stops a player from resolving.

---

## 1. Background: how a name becomes a `canonical_id`

Every outside source (ESPN, Yahoo, Sleeper, FFB, UDK) refers to players by
**name**. Internally the app keys everything off a stable **`canonical_id`** —
which is nflreadpy's `gsis_id` (e.g. `00-0036973`), *not* the display name,
because names collide and change formatting between releases.

Turning a source name into a `canonical_id` happens in two steps
(`repositories/player_identity_repo.py` → `resolve_many_with_fallback`):

1. **Manual map first** — look the name up in the `player_id_map` collection,
   keyed by `(source, source_name)`. This is the hand-curated override table.
2. **Exact-name fallback second** — if there's no map row, try an **exact**
   (not fuzzy) `display_name` + `position` match against nflreadpy's player
   list (`resolve_by_display_name`).

If **both** steps fail, the player gets `canonical_id = NaN` and is **silently
dropped** from whatever was being built (roster, ADP comparison, projections).
That silent drop is the symptom you're chasing.

> Why players go missing: the fallback is exact, so `"Travis Etienne Jr."`
> (what most vendors say) does **not** match nflreadpy's `"Travis Etienne"`.
> One character of difference is enough to break it. The manual map exists to
> record exactly these exceptions.

**Which `source` string goes with which data source** (you'll need this for the
map's `source` column):

| Source data        | `source` key | Resolved in                          |
| ------------------ | ------------ | ------------------------------------ |
| UDK rankings       | `udk`        | `services/roster_service.py`         |
| FFB projections    | `ffb`        | `services/projections_service.py`    |
| ESPN ADP           | `espn`       | `services/adp_comparison_service.py` |
| Sleeper ADP        | `sleeper`    | `services/adp_comparison_service.py` |
| Yahoo ADP          | `yahoo`      | `services/adp_comparison_service.py` |

> **`udk` is special.** UDK rankings define the app's entire *player universe*.
> If a player fails to resolve for `udk`, they vanish from **everything**
> (Player Profile dropdown, Draft Plan, Team Profile). The other sources only
> affect that source's specific view (e.g. a missing `espn` row just leaves a
> blank in the ADP comparison).

---

## 2. Find out *who* is missing

Each service exposes an `unresolved()` helper that lists names failing **both**
resolution steps — i.e. the names that need a manual map row. Run this whenever
you suspect a player is missing (needs `MONGODB_URI` set, same as running the
app):

```bash
python -c "
from app_context import AppContext
ctx = AppContext([2024])

# Each list = source names that couldn't be matched to any canonical_id.
print('UDK   :', ctx.roster_service.unresolved())
print('FFB   :', ctx.projections_service.unresolved())
print('ESPN  :', ctx.adp_comparison_service.unresolved('espn'))
print('SLEEPER:', ctx.adp_comparison_service.unresolved('sleeper'))
print('YAHOO :', ctx.adp_comparison_service.unresolved('yahoo'))
"
```

Anything printed here is a candidate for a `player_id_map` row (or is a player
nflreadpy genuinely has no record of yet — e.g. a very recent
signing/draftee, in which case there's nothing to map to yet).

---

## 3. Find the `canonical_id` for a player

The canonical ID lives in nflreadpy's player list, which does **not** need the
database — you can look it up directly. Search by a **last-name substring** so
formatting differences (`Jr.`, punctuation) don't hide the row:

```bash
python -c "
import nflreadpy as nfl
p = nfl.load_players().to_pandas()

# Change 'Etienne' to whatever last name you're hunting for.
hits = p[p['display_name'].str.contains('Etienne', case=False, na=False)]
print(hits[['gsis_id', 'display_name', 'position', 'latest_team']].to_string(index=False))
"
```

Example output:

```
   gsis_id   display_name position latest_team
00-0036973 Travis Etienne       RB          NO
00-0040644 Trevor Etienne       RB         CAR
```

**Pick the right row using position + team**, not just the name — note how
`Travis` and `Trevor` Etienne are both RBs and easy to confuse. The `gsis_id`
of the correct row (here `00-0036973`) is the `canonical_id` you'll write into
the map.

> If the search returns **nothing**, nflreadpy has no record of that player yet.
> Don't invent an ID — wait until they appear in a future nflreadpy refresh
> (`ctx.nfl_read_repo.refresh_players()`), then map them.

---

## 4. Add the mapping row(s)

The overrides live in `data/player_id_map.csv`. It's loaded into the
`player_id_map` collection like every other CSV, by `scripts/load_data.py`
(filename stem = collection name). The file has exactly three columns:

```csv
source,source_name,canonical_id
udk,Travis Etienne Jr.,00-0036973
ffb,Travis Etienne Jr.,00-0036973
espn,Travis Etienne Jr.,00-0036973
yahoo,Travis Etienne Jr.,00-0036973
```

Rules for the rows:

- **One row per source** that has the problem — the map is keyed by
  `(source, source_name)`, so each source needs its own row even when the
  misspelled name is identical.
- **`source_name` must match the source's spelling *exactly*** (including
  `Jr.`, capitalization, and punctuation) — this is the string the map looks
  up. Copy it verbatim from the `unresolved()` output in Step 2.
- **Skip sources that already resolve.** In the Etienne example, Sleeper says
  `"Travis Etienne"`, which matches nflreadpy via the fallback — so there's no
  `sleeper` row. Adding one would be harmless but pointless.
- **`canonical_id`** is the `gsis_id` from Step 3, kept as-is (it's a string
  like `00-0036973`, not a number).

Not sure which sources spell it wrong? Grep the raw data files:

```bash
grep -in "etienne" data/*.csv data/ffb/*.csv
```

---

## 5. Load and verify

Reload the data so the new rows land in the `player_id_map` collection
(`MONGODB_URI` must be set; this reloads every collection from `data/`, which
is safe and idempotent):

```bash
python scripts/load_data.py
```

Then confirm the fix — the player should no longer be listed as unresolved:

```bash
python -c "
from app_context import AppContext
ctx = AppContext([2024])
print('Still unresolved in UDK:', ctx.roster_service.unresolved())
"
```

Finally, open the app (`streamlit run streamlit_app.py`) and check that the
player now shows up where they belong (e.g. the Player Profile dropdown, or the
relevant team's roster on the Team Profile page).

---

## Quick checklist

1. Run the `unresolved()` snippet → note the exact missing name(s).
2. Look up the `gsis_id` in nflreadpy; confirm position + team.
3. Add one `source,source_name,canonical_id` row per affected source to
   `data/player_id_map.csv`.
4. `python scripts/load_data.py`.
5. Re-run `unresolved()` to confirm it's gone; spot-check in the app.

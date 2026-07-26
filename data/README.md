# DataHuddle Data Reference

This folder is the **source of truth** for all fantasy football data in the app.

How it works: every `.csv` file in this folder (including subfolders like `ffb/`)
maps to one MongoDB collection, **named after the filename** without its extension
— `espn_projections.csv` becomes the `espn_projections` collection. Running:

```bash
python scripts/load_data.py
```

wipes each collection and refills it from its CSV, so the database always mirrors
this folder exactly. It's safe to re-run any time. (`data/raw/` is skipped — it
holds scratch inputs like saved web pages, never loaded to Mongo.)

⚠️ **Filenames matter.** Renaming a CSV changes which collection it loads into.
In particular, `FfbData` in `data_manager.py` reads the `ffb_qb_projections` and
`ffb_flex_projections` collections by name — don't rename those files.

---

## Files

### `espn_projections.csv` — ESPN season projections + ADP
- **Source:** ESPN's fantasy API, via `python scripts/scrape_espn_projections.py`
- **Contents (~373 players, QB/RB/WR/TE):** 2026 season stat projections
  (passing, rushing, receiving, fumbles), ESPN's ADP, ESPN's own projected
  fantasy points, and computed half/full-PPR fantasy points (season + per-game,
  using the same scoring rules as `data_manager.py`).
- **Quirks:**
  - ESPN publishes only **one** ADP (no scoring-format split), so
    `half_ppr_adp` and `full_ppr_adp` intentionally contain the same value.
  - `projected_fantasy_points` is ESPN's own number (full-PPR default scoring).

### `sleeper_projections.csv` — Sleeper season projections + ADP
- **Source:** Sleeper's public API, via `python scripts/scrape_sleeper_projections.py`
- **Contents (~561 players, QB/RB/WR/TE):** same column layout as the ESPN file —
  stat projections, ADP, Sleeper's own projected points (`projected_fantasy_points`
  = Sleeper's full-PPR number), computed half/full-PPR points.
- **Quirks:**
  - `half_ppr_adp` and `full_ppr_adp` are **genuinely different** here (Sleeper
    tracks ADP per scoring format).
  - No `targets` column — Sleeper doesn't project targets.
  - Covers ~200 more players than ESPN; expect Sleeper-only names when joining.

### `yahoo_draftanalysis.csv` — Yahoo draft rankings + ADP
- **Source:** Yahoo's Draft Analysis page. It's login-gated and JavaScript-rendered,
  so it can't be scraped by a normal script — instead, a snippet is pasted into the
  browser console (see update steps below), which pages through the table and
  copies a CSV to the clipboard.
- **Contents (~1,148 players, all positions incl. K/DEF):** `yahoo_rank` (Yahoo's
  "Overall Rank" — the default order in their live draft lobby), `percent_drafted`,
  `preseason_adp`, and `adp` ("All Drafts").
- **Quirks:**
  - Yahoo's ADP here is **standard scoring** (per the page's own footnote) — not
    directly comparable to the half/full-PPR ADP columns from Sleeper.
  - ~80% of rows have blank ADP — normal; Yahoo only shows ADP for players
    actually being drafted.
  - Rank numbers have gaps (e.g. max rank ~2767 across ~1148 rows) — ordering is
    still correct.
  - Some players carry dual positions like `RB,TE`.
  - Pos Rank / CER / "Plus ADP" columns on the page are locked behind Yahoo
    Fantasy Plus and are not captured.

### `ffb/` — Fantasy Footballers (Ultimate Draft Kit)

#### `ffb/ffb_qb_projections.csv`, `ffb/ffb_flex_projections.csv`
- **Source:** Fantasy Footballers projections, manually placed here.
- **Contents:** season stat projections — QB file: passing + rushing; flex file
  (RB/WR/TE): rushing + receiving — plus rank, PPG, bye week.
- **Quirks:** the headers contain **duplicate column names** (`YDS`, `TDS` appear
  twice — once for each stat category). Pandas renames the second occurrence to
  `YDS.1`/`TDS.1` on read, and `FfbData` in `data_manager.py` depends on that
  exact column order — don't reorder columns in these files.

#### `ffb/udk_qb_rankings_ppr.csv`, `udk_rb_...`, `udk_wr_...`, `udk_te_...`
- **Source:** Ultimate Draft Kit site → manual CSV export per position.
- **Contents (35 QB / 89 RB / 127 WR / 52 TE):** UDK's PPR rankings with
  `Rank`, `Points` (projected season fantasy points), `Risk` and `Upside` scores,
  `ADP`, `Tier`, plus two long-form text columns: `Outlook` (season analysis
  write-up) and `Dynasty` (dynasty-league note).
- **Quirks:** the `Markers` column is leftover UI text from the export
  ("Mark Keeper / Mark Favorite / ...") — ignore it.

### `raw/` — scratch area (not loaded)
Saved HTML pages and other intermediate inputs live here. `load_data.py`
deliberately skips this folder.

---

## How to update the data

Do whichever sources you want to refresh, then load. ADP and rankings shift
constantly during draft season, so ESPN/Sleeper/Yahoo are worth refreshing
regularly; the Fantasy Footballers files only change when they publish updates.

**1. ESPN** (seconds, fully automatic):
```bash
python scripts/scrape_espn_projections.py
```

**2. Sleeper** (seconds, fully automatic):
```bash
python scripts/scrape_sleeper_projections.py
```

**3. Yahoo** (a few minutes, needs your browser):
1. Log into Yahoo and open
   https://football.fantasysports.yahoo.com/f1/draftanalysis —
   set the position filter to **ALL**, stay on page 1.
2. Open DevTools (⌘⌥I) → Console tab. Paste the entire contents of
   `scripts/yahoo_draftanalysis_console.js` and press Enter.
   (If Chrome blocks pasting, type `allow pasting` first and retry.)
3. Wait while it clicks through all ~39 pages (progress logs per page).
4. When done it copies the CSV to your clipboard — if it says the clipboard
   was blocked, type `copy(window.__yahooCSV)` in the console.
5. Paste into a new file and save as `data/yahoo_draftanalysis.csv`
   (overwrite the old one).

**4. Fantasy Footballers / UDK** (manual):
1. Log into the UDK site
2. Navigate to [Andy's Projections](https://www.thefantasyfootballers.com/2026-ultimate-draft-kit/udk-andys-projections/)
3. Export QB, Flex for Full-PPR (6pt QB TD) to CSV, drop files into `data/ffb/` using **exact existing filenames** (`ffb_{qb/flex}_projections.csv`)
4. Navigate to [Player Rankings](https://www.thefantasyfootballers.com/2026-ultimate-draft-kit/udk-position-rankings/?position=QB)
5. Export {QB, RB, WR, TE} for Full-PPR (6pt QB TD) to CSV, drop files into `data/ffb/` using **exact existing filenames** (`udk_{qb/rb/wr/te}_rankings_ppr.csv`)
Log into the UDK site, export each position's rankings CSV, and drop the files
into `data/ffb/` using the **exact existing filenames** (e.g.
`udk_rb_rankings_ppr.csv`), overwriting the old ones.

**5. Load everything into MongoDB:**
```bash
python scripts/load_data.py
```
Requires the `MONGODB_URI` environment variable (in `~/.zshrc`). You should see
one `Loaded N rows into '<collection>'` line per CSV — currently 9 collections.

---

## Gotchas

- **Wipe-and-replace:** each load deletes a collection's contents before
  re-inserting. That's by design (this folder is the source of truth), but it
  means anything written directly to those collections outside this flow is lost
  on the next load.
- **Same filename in two folders = collision.** Collections are named by filename
  stem only, so `data/a/rankings.csv` and `data/b/rankings.csv` would overwrite
  each other. Keep filenames unique across all subfolders.
- **Player names differ across platforms** (spelling, suffixes, defenses).
  Joining ESPN/Sleeper/Yahoo/UDK data by name will have misses — the planned
  `player_id_map` collection (see `PLANNING.md`) is the intended fix.
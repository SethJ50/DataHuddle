# Updating the app's data

Everything the app reads comes from one of three places:

| Source | How it arrives | Needs a refresh |
|---|---|---|
| **nflreadpy** — game logs, play-by-play, snaps, schedules, tracking | Downloaded automatically on first use | Never manually; see [Caching](#caching) |
| **MongoDB collections** — projections, rankings, ADP, salaries | You run a loader script | Whenever a source publishes |
| **Simulation artifacts** — `data/sim/*.npz` | You run the simulator | After any data or draft-settings change |

Set `MONGODB_URI` in your environment before running anything below.

---

## 1. Pre-draft: the seasonal refresh

Do this when rankings or projections are updated — a few times through the summer,
then rarely.

### a. Files you place by hand

| File | Where from |
|---|---|
| `data/ffb/ffb_qb_projections_{andy,mike,jason}.csv` | Fantasy Footballers UDK export, per analyst |
| `data/ffb/ffb_flex_projections_{andy,mike,jason}.csv` | Same |
| `data/ffb/udk_{qb,rb,wr,te,k,dst}_rankings_ppr.csv` | UDK rankings export |
| `data/player_id_map.csv` | Curated by you; only edit to fix a name that will not resolve |

### b. Files a script fetches

```bash
python scripts/scrape_espn_projections.py --season 2026   # -> data/espn_projections.csv
python scripts/scrape_sleeper_projections.py --season 2026 # -> data/sleeper_projections.csv
```

Yahoo runs in the browser — the table is login-gated, JavaScript-rendered and
paginated 30 players at a time, so nothing can fetch it:

1. Open `football.fantasysports.yahoo.com/f1/draftanalysis`, set the position
   filter to **ALL**, and stay on page 1.
2. DevTools (`Cmd+Option+I`) → **Console**.
3. Paste all of `scripts/yahoo_draftanalysis_console.js` and press Enter.
   If Chrome refuses the paste, type `allow pasting` first and retry.
4. Let it page through — it logs progress. At the end it copies the CSV to your
   clipboard; if that's blocked it tells you to type `copy(window.__yahooCSV)`.
5. Paste into a new file and save as `data/yahoo_draftanalysis.csv`.

Two things about what comes out: locked Fantasy Plus cells (Pos Rank, CER, Plus
ADP) are blank, and **Yahoo's ADP on this page is standard scoring** — the app
already treats Yahoo as format-agnostic for exactly this reason.

<sub>`scripts/parse_yahoo_draftanalysis.py` is the older route, parsing a saved
copy of the page's HTML. It still works if you ever need it, but the console
script replaces it.</sub>

### c. Check before loading

```bash
python scripts/check_data_files.py
```

**Do not skip this.** The Fantasy Footballers exports contain duplicate `YDS` and
`TDS` headers — one pair rushing, one receiving — and the adapter maps them *by
position*. A file whose columns are all present but reordered loads without
complaint and silently attributes receiving yards to rushing.

### d. Load

```bash
python scripts/load_data.py
```

Wipes and refills one collection per CSV, named after the filename stem
(`espn_projections.csv` → `espn_projections`). Also pulls the current season's
ADP from Fantasy Football Calculator, which is the app's only source of
draft-position spread. Safe to re-run.

`data/raw/` and `data/dfs/` are skipped — the first is scratch input, the second
has its own loader.

> **Renaming a CSV changes which collection it loads into.** Don't.

---

## 2. Simulations

Re-run after any pre-draft data load, and after changing a draft's settings —
teams, rounds, scoring, platform or keepers all change the fingerprint the app
looks the artifact up by.

```bash
python scripts/run_draft_sim.py --list              # which drafts exist
python scripts/run_draft_sim.py --all               # every saved draft
python scripts/run_draft_sim.py --all --skip-existing
python scripts/run_draft_sim.py --draft-id abc123 --n-sims 20000
```

Writes `data/sim/<draft>_<fingerprint>.npz`. If a page says *"No availability
data for these draft settings"*, this is what it wants.

Changing your **draft position** or **lineup slots** does *not* invalidate the
artifact — those change which columns you look at, not how the draft unfolds.

---

## 3. Daily Fantasy: the weekly refresh

Do this each week once the slate is posted.

1. Download both exports into `data/dfs/`:
   - DraftKings → `DKSalaries.csv`
   - FanDuel → `FDSalaries.csv`
2. Load them:

```bash
python scripts/load_salaries.py --dry-run   # check the week it derived
python scripts/load_salaries.py
```

The week is **derived, not typed**: DraftKings' export carries a kickoff date,
matched against the NFL schedule. FanDuel's has no date and inherits that answer.
Override only if DraftKings changes format or you have the FanDuel file alone:

```bash
python scripts/load_salaries.py --season 2026 --week 5
```

Every week loaded is kept, so history accumulates rather than being replaced.

---

## Caching

nflreadpy caches **in memory only** by default, so every `streamlit run`
re-downloads every source — and `load_ff_playerids` alone takes about two
minutes. Switch it to disk:

```bash
export NFLREADPY_CACHE=filesystem
```

Cold start drops from ~2.5 minutes to a few seconds. The default expiry is 24
hours; `NFLREADPY_CACHE_DURATION` takes seconds if you want longer.

Streamlit's own `@st.cache_data` only helps *within* a run — it cannot help the
first load after a restart, which is exactly when you feel this.

---

## Quick reference

```bash
# Seasonal — after new rankings or projections
python scripts/scrape_espn_projections.py --season 2026
python scripts/scrape_sleeper_projections.py --season 2026
# Yahoo: paste scripts/yahoo_draftanalysis_console.js into the browser console,
#        save the result as data/yahoo_draftanalysis.csv
python scripts/check_data_files.py
python scripts/load_data.py
python scripts/run_draft_sim.py --all

# Weekly — after downloading DKSalaries.csv and FDSalaries.csv into data/dfs/
python scripts/load_salaries.py

# One-time, insurance only — historical FFC ADP into adp_snapshots
python scripts/ingest_ffc_history.py
```

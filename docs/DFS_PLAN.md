# DataHuddle — DFS Section Build Plan

The second half of the app: a **DFS** category sitting beside **Pre-Draft**, with
four pages under it now and two more later.

Everything in Part 0 was measured against the real nflreadpy data before anything
here was recommended. Where a measurement changed the design, the measurement is
shown.

---

# Part 0 — What was measured

Run on `nflreadpy 0.1.5`, season 2024 unless stated.

| Source | Rows × cols | Load | Memory |
|---|---|---|---|
| `load_pbp` | 49,492 × 372 | 1.6s | **372 MB** |
| `load_pbp`, pruned to 21 columns | 49,492 × 21 | 1.3s | **21 MB** |
| `load_ff_opportunity` (weekly) | 6,005 × 159 | 0.8s | 9.4 MB |
| `load_snap_counts` | 26,615 × 16 | 0.2s | 13.5 MB |
| `load_pfr_advstats` (rec) | 4,453 × 17 | 0.2s | 2.1 MB |
| `load_nextgen_stats` (receiving) | 1,435 × 23 | 0.2s | 0.8 MB |
| `load_schedules` | 285 × 46 | 0.2s | 0.4 MB |

**The one finding that shapes everything: play-by-play is 372 MB per season.**
Three seasons unpruned would be over a gigabyte held in memory forever. Selecting
the ~21 needed columns *in Polars, before* `.to_pandas()` drops it to 21 MB — an
18× cut — because the conversion never materialises the other 351 columns. And
the table the plots actually draw from, neutral-script pass rate per team per
week, is **43 KB**. Play-by-play is a means to an end.

**Corrections to `data_source_overviews/nflreadpy.md`:**

- The loader is `load_nextgen_stats`, not `load_next_gen_stats`.
- `load_participation` works, contrary to its reputation — 46,168 rows for 2023
  and 45,919 for 2024. It is usable if we want coverage and personnel data later.
- The doc lists `nfl.load_player_stats(summary_level=[...])`. In 0.1.5 the
  signature is positional seasons; `load_stats` is the newer general entry point.

**`ff_opportunity` is full PPR.** Its `total_fantasy_points` differs from
`player_stats.fantasy_points_ppr` by a mean of 0.045 and from
`fantasy_points` (standard) by 2.056. That matters because you play FanDuel,
which is half-PPR — see §2.4.

**2025 is complete** — weeks 1 through 22, playoffs included.

## 0.1 The join-key trap

This is the classic nflverse problem and it lands directly on the Player Profile.
Sources do **not** share an id:

| Source | Its id column | Joins to `canonical_id`? |
|---|---|---|
| `player_stats` | `player_id` | Yes, directly |
| `ff_opportunity` | `player_id` | Yes, directly |
| `nextgen_stats` | `player_gsis_id` | Yes, after a rename |
| `injuries`, `depth_charts` | `gsis_id` | Yes, after a rename |
| **`snap_counts`** | **`pfr_player_id`** | **No** |
| **`pfr_advstats`** | **`pfr_player_id`** | **No** |

Snap share is in the form strip, so this must be solved. The bridge is
`load_ff_playerids()` — 12,470 rows, of which 7,797 carry both `gsis_id` and
`pfr_id`.

Measured coverage against 2024 snap counts:

```
all positions          1,782 of 2,192 pfr ids resolve   (81.3%)
QB / RB / WR / TE        615 of   620 pfr ids resolve   (99.2%)
                          22 of 7,202 snap rows unresolved (0.3%)
```

The five skill-position misses are practice-squad tight ends — John Samuel
Shenker at 65 snaps for the season is the largest. **This is a non-issue for DFS**,
but the unresolved rows must be dropped explicitly rather than silently becoming
NaN, or a missing snap count will read as "did not play".

---

# Part 1 — Decisions taken

| Decision | Choice | Why |
|---|---|---|
| **Seasons** | 2023–2025 | ~63 MB pruned pbp. DFS is recency-driven; three seasons gives defensive baselines and year-over-year context without carrying weight that never gets plotted. Kept SEPARATE from the season-long `SEASONS` list, which stays at six. |
| **pbp storage** | Pruned, held in memory | Lazy-loaded and cached like `player_stats` already is. Aggregates derive on demand in ~5 ms, so adding a new plot later is one `groupby` rather than a schema migration. |
| **Player Profile** | Sketch + form strip | Your layout, plus a rolling-form summary above the gamelog tabs. |
| **Team Profile** | Offense / Defense tabs | Covers both reasons to look a team up: stacking their offense, and targeting what they allow. |
| **Cheat Sheet** | Season + week, filterable | Works today on historical data with no salary files. A salary column appears later without a redesign. |
| **Scoring** | FanDuel half-PPR default, PPR toggle | Matches the contests you actually enter, and the xFP conversion is exact. |
| **Build mode** | Phase by phase, I implement | Same rhythm as the Draft Runner advanced phases. |

---

# Part 2 — The data layer

## 2.1 What each page needs

| Page | Sources |
|---|---|
| Basic Plots — Actual vs xFP | `ff_opportunity` |
| Basic Plots — Neutral pass rate | `pbp` |
| Basic Plots — FP/rush vs EPA/rush allowed | `pbp` + `player_stats` |
| Basic Plots — FP/att vs EPA/att allowed | `pbp` + `player_stats` |
| Player Profile | `ff_opportunity`, `player_stats`, `snap_counts`, `nextgen_stats`, `pfr_advstats`, `ff_playerids` |
| Team Profile — offense | `pbp`, `player_stats`, `ff_opportunity`, `snap_counts` |
| Team Profile — defense | `pbp`, `player_stats` |
| Cheat Sheet | everything above |
| League View | your own Mongo collection |

Only `pbp` is expensive. Everything else is under a second and under 15 MB.

## 2.2 Three tiers

The existing app already works this way, and the DFS half should match it:

```
  nflreadpy  ──►  DfsReadRepo      raw tables, lazy, cached once per process
                       │            (pbp pruned at load)
                       ▼
                  services/        small tidy frames, one question each
                       │            (43 KB, not 21 MB)
                       ▼
                   pages/          thin Streamlit scripts, no data logic
```

The rule that keeps this honest: **a page never touches `DfsReadRepo` directly.**
If a page needs a number, a service produces it. That is what makes the numbers
testable without Streamlit, exactly as `draft_model` and `services` are today.

## 2.3 New modules

**`repositories/dfs_read_repo.py`** — mirrors `NflReadRepo`: lazy getters plus
matching `refresh_*` methods.

```
pbp()                  pruned at load, the only expensive one
ff_opportunity()       weekly expected points
snap_counts()
nextgen_stats(type)    passing / rushing / receiving
pfr_advstats(type)     pass / rush / rec
schedules()            Vegas lines, opponent, home/away
player_id_crosswalk()  pfr_id -> gsis_id, from load_ff_playerids
```

**`services/dfs_scoring.py`** — converts PPR figures to FanDuel half-PPR. Small,
pure, heavily tested. See §2.4.

**`services/dfs_player_service.py`** — the player-week frame: one row per
player per week with everything joined and scored. This is the single table the
Player Profile, the Cheat Sheet and half the Team Profile all read.

**`services/dfs_team_service.py`** — the pbp aggregates: offensive tendencies
(pace, neutral pass rate, PROE) and defensive allowances (EPA and fantasy points
by position).

**`presentation/dfs_charts.py`** and **`presentation/dfs_tables.py`** — Altair
specs and column configs, following `presentation/charts.py` and the validated
palette in `presentation/colors.py`.

**Pages:** `dfs_basic_plots.py`, `dfs_player_profile.py`, `dfs_team_profile.py`,
`dfs_cheat_sheet.py`.

**Config:** `DFS_SEASONS = [2023, 2024, 2025]` beside the existing `SEASONS` in
`streamlit_state.py`.

## 2.4 Scoring

FanDuel is half-PPR, which the app already models —
`scoring.ScoringFormat.HALF_PPR` exists and `scoring.fantasy_points` already
implements it. No new scoring engine is needed, only a conversion for the
*expected* numbers, which arrive pre-scored in PPR.

The conversion is exact, because `ff_opportunity` ships both `receptions_exp`
and `pass_interception_exp`:

```
fanduel_xfp = total_fantasy_points_exp - 0.5 * receptions_exp + 1 * pass_interception_exp
fanduel_fp  = total_fantasy_points     - 0.5 * receptions     + 1 * pass_interception
```

**TWO terms, not one.** Phase 3 found that the source scores an interception at
**-2** while FanDuel scores it at **-1** -- verified by solving for the source's
own coefficients, which come out as passing yards 0.04, passing touchdowns 4,
interceptions -2, receptions 1. Receptions alone is right for backs, receivers
and tight ends and leaves every quarterback a point light per interception.

Everything else agrees: yards, touchdowns, two-point conversions and lost fumbles
are scored identically, so nothing else needs adjusting.

**Both sides must come from the same source.** It is tempting to take actual
points from `player_stats` and expected from `ff_opportunity`, but they disagree
slightly on two-point conversions and fumbles, and the whole plot is a
*difference* between the two. A 0.3-point sourcing artefact would be invisible
and would sit inside the number the page exists to show. Take both from
`ff_opportunity`.

**DraftKings is deliberately out of scope for now.** DK is full PPR plus 3-point
bonuses at 100 rushing yards, 100 receiving yards and 300 passing yards. There is
no expected-value column for a bonus, so a DK xFP would have to model
`P(yards >= 100)` per player per week. That is a real modelling task, not a
conversion, and it would be the only approximate number on the page. If you want
DK later it deserves its own phase.

## 2.5 Neutral script, defined

"Neutral script" has no single agreed definition, so the plan fixes one and makes
it visible on the page rather than burying it:

```
win probability between 20% and 80%     not garbage time either way
quarters 1-3                            fourth-quarter play calling is score-driven
play_type in (pass, run)                excludes punts, kicks, timeouts
excludes kneels and spikes              clock management, not play calling
```

These become named constants with the reasoning in the docstring, and the page
caption states them. A pass-rate number whose filter you cannot see is not worth
much, and yours should not disagree with a site's without you knowing why.

**PROE** (pass rate over expected) comes free: `pbp` carries `xpass`, the model's
predicted pass probability, so PROE is `mean(pass) - mean(xpass)` over the same
filtered set. It is a better tendency measure than raw pass rate because it
adjusts for down, distance and score, and it costs nothing extra.

---

# Part 3 — Page designs

## 3.1 Basic Plots

One page, a plot-type dropdown, and filters that change with the selection.

```
┌──────────────────────────────────────────────────────────────────┐
│  ▼ Plot:  Actual FPTS vs Expected (xFP)                          │
│  ▼ Season 2025   ▼ Weeks 1-18   ▼ Pos RB,WR,TE   ▼ Rush/Rec/Both │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│            [ scatter, xFP on x, actual FP on y ]                 │
│            diagonal y=x line; above it = outperforming           │
│            point colour = position (validated palette)           │
│            hover = player, team, both numbers, the gap           │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  Neutral script: WP 20-80%, Q1-Q3, kneels and spikes excluded.   │
└──────────────────────────────────────────────────────────────────┘
```

The four plots at launch:

1. **Actual FPTS vs xFP** — scatter, one point per player over the week range,
   with a `y = x` reference line. Above the line means outperforming expectation.
   Filters: rushing / receiving / both, position, season, week range.
2. **Neutral Script Pass Rate by Team** — horizontal bars, 32 teams sorted, with
   PROE available as a second view. Filters: season, week range.
3. **FPTS/rush vs EPA/rush allowed** — scatter, one point per *defense*. Finds
   the teams that give up fantasy points on the ground.
4. **FPTS/pass att vs EPA/pass att allowed** — same for the air.

Plots 3 and 4 are the same shape with different inputs, so they share one
function. Adding a fifth plot later is a new entry in a registry dict plus its
filter set — the "*more in future*" line in your notes is a design requirement,
so the dropdown is data-driven from the start rather than an if/elif chain.

Charts follow the `dataviz` conventions already used in the Draft Runner: the
validated position palette, thin marks, a legend whenever there are two or more
series, hover tooltips, and no dual axes.

## 3.2 Player Profile

Your sketch plus the form strip.

```
┌────────────────┬──────────────────────────────────────────────────┐
│  [ headshot ]  │  L5:  14.2 FP   |   xFP 12.8   |   +1.4 / game   │
│                │  snaps 78%   target share 24%   aDOT 11.2        │
│  ▼ Player      ├──────────────────────────────────────────────────┤
│                │  [Usage] [Efficiency] [Expected] [Advanced]      │
│  ── SALARIES ──│   wk  opp   snp  tgt  rec  yds  td   FP    xFP   │
│  FD    $7,400  │   18  @SF    61    9    7   88   1  21.8   15.2  │
│  DK    $6,900  │   17   KC    58    7    5   54   0  10.4   12.1  │
│  own%    12.4  │   16  @LV    63   11    8  102   1  24.2   17.9  │
│                │   15  DEN    60    6    4   41   0   8.1    9.8  │
│  ── CONTEXT ── │   14 @ARI    55    8    6   77   1  19.7   14.3  │
│  bye      wk 9 │                                                  │
│  status  ACT   │                                                  │
└────────────────┴──────────────────────────────────────────────────┘
```

**The form strip** answers "is he trending?" without reading rows. Rolling last-5
fantasy points, expected points, the gap between them, snap share, target share
and aDOT. The gap is the interesting one: a player beating expectation for five
weeks is either genuinely good or about to regress, and the raw gamelog makes you
compute that in your head.

**The gamelog tabs**, split by what question each answers:

| Tab | Columns | Question |
|---|---|---|
| **Fantasy** | att, yds, paTds, rush att, rush yds, rush tds, tgts, rec, yards, rec tds, fpts | adjust what is shown based on position |
| **Usage** | snaps, snap %, targets, target share, carries, RZ touches, air yards share, WOPR | How much of the offense runs through him? |
| **Efficiency** | yards, YAC, yards/target, EPA, RACR, PACR | What does he do with it? |
| **Expected** | rush/rec/total xFP, actual, the diff | Is he earning his production or lucky? |
| **Advanced** | separation, cushion, aDOT, YAC over expected, drop %, yards before/after contact | The scouting layer — NGS and PFR |

Five tabs rather than one wide table because these are five different
questions, and a 30-column table answers none of them quickly. Fantasy is the
plain box score -- what he actually did -- and the other four each take one angle
on why.

**Position-aware**, like the existing `GameLogView` already is: a quarterback's
Usage tab shows dropbacks and attempts, not target share.

The **salaries** block and **ownership %** are placeholders until the files
arrive. They render as "not loaded" rather than being hidden, so the layout does
not jump when Phase 10 lands.

## 3.3 Team Profile

One team at a time, two tabs.

```
┌──────────────────────────────────────────────────────────────────┐
│ [logo] SEATTLE SEAHAWKS     ▼ Team   ▼ Season   ▼ Week range      │
├─────── [ OFFENSE ] ──── [ DEFENSE ] ─────────────────────────────┤
│  pace 27.4 s/play    neutral pass 58% (8th)    PROE +2.1         │
│  plays/g 64.2        RZ trips/g 3.4            implied tot 22.5  │
├──────────────────────────────────────────────────────────────────┤
│  WHO GETS THE BALL                                               │
│  player          tgt%   car%   RZ%   aDOT   FP/g   xFP/g   snap% │
│  JSN               26      0    18    9.4   14.2    13.1     88  │
│  K. Walker          9     61    44    0.6   13.8    14.9     62  │
│  Z. Charbonnet      5     28    31    0.4    7.1     6.8     34  │
├──────────────────────────────────────────────────────────────────┤
│  [ pass rate by week — line ]    [ target share by week — area ] │
└──────────────────────────────────────────────────────────────────┘
```

**Offense tab** — a tendency strip (how fast, how pass-happy, how often in the red
zone), then the usage table, then two trend charts. The usage table is the heart
of it: DFS is a question of who gets the ball, and every column there is a share
rather than a total so a player who missed a game is not penalised.

**Defense tab** — same shape, inverted. What they allow: EPA per rush and per
pass attempt, fantasy points allowed per position, pass rate faced (teams that
get run on are teams that are ahead), and rank-in-league for each so a number
comes with context.

Ranks matter more than raw values here. "EPA/pass allowed +0.09" means nothing on
its own; "+0.09, 31st" means everything.

## 3.4 Cheat Sheet

One row per player, one column per statistic, with the columns you want turned on
and everything else off.

```
┌──────────────────────────────────────────────────────────────────┐
│ ▼ Season 2025  ▼ Week 18  ▼ Pos: all  ▼ Teams: all               │
│ Columns: [x] FP  [x] xFP  [x] snap%  [ ] aDOT  [ ] EPA  [x] tgt% │
├──────────────────────────────────────────────────────────────────┤
│ player          pos  team  opp    FP    xFP   snap%   tgt%       │
│ ...             sortable, one row per player                     │
└──────────────────────────────────────────────────────────────────┘
```

The whole page is one read of the player-week frame from
`dfs_player_service`, so it is cheap. The work is the **column registry**: a
declarative list of every available column with its label, format, help text and
which source it comes from. A multiselect drives which are shown.

Building it as a registry rather than a fixed table is what makes this page grow
for free — a new statistic anywhere in the service becomes a new checkbox.

**Salary and value columns** (points per $1,000) slot in at Phase 10 as two more
registry entries.

## 3.5 League View — later

Week-by-week finishes for every team in your FanDuel league, entered by hand into
a Mongo collection. Metrics worth showing: cumulative points, finish
distribution, weekly rank, head-to-head record, consistency (standard deviation
of weekly finish), and best/worst weeks.

This needs no nflreadpy at all — it is your own data, a repository, a service and
a page. It is genuinely independent of everything above, which is why it is last:
it can be built any time without blocking or being blocked.

---

# Part 4 — The phases

Ordered so that each phase produces something visible, and so the expensive and
uncertain work comes after the cheap and certain work.

### Phase 1 — The DFS shell
Add the `DFS` category to `st.navigation` beside `Pre-Draft`, with four pages that
render a heading and a "coming soon" line. Add `DFS_SEASONS` to
`streamlit_state.py`.

*Small on purpose.* It makes the navigation change reviewable on its own, and
every later phase has somewhere to put its work.

### Phase 2 — `DfsReadRepo`
The raw layer: lazy getters for all seven sources, pbp pruned at load, plus the
`pfr_id → gsis_id` crosswalk with the unresolved rows dropped explicitly.

Tests: pruning keeps the needed columns; the crosswalk resolves the skill
positions; a missing id is dropped rather than silently NaN.

### Phase 3 — Scoring conversion
`dfs_scoring.py`: PPR → FanDuel half-PPR for both actual and expected, reusing
`ScoringFormat.HALF_PPR`.

Tests: conversion is exact against a hand-worked example; a player with no
receptions is unchanged; both sides of the actual/expected pair convert
consistently.

### Phase 4 — Basic Plots, plot 1 (Actual vs xFP)
The page shell, the plot registry, the filter machinery, and the first plot.

*This is the phase that proves the whole stack* — navigation, repo, scoring,
service, chart, page — using the cheapest source. If something about the design
is wrong, it surfaces here rather than in Phase 8.

### Phase 5 — `dfs_team_service`, offensive half
The pbp aggregates: pace, neutral pass rate, PROE, plays per game, red-zone
trips. The neutral-script filter becomes named constants here.

Tests: the neutral filter excludes what it should; PROE is pass rate minus xpass;
a team with no qualifying plays yields NaN, not zero.

### Phase 6 — Basic Plots, plots 2–4
Neutral pass rate by team, and the two defence scatters. Needs the defensive half
of `dfs_team_service` — EPA and fantasy points allowed, per position.

### Phase 7 — `dfs_player_service` + Player Profile
The player-week frame, joining six sources, then the page: headshot, dropdown,
salary placeholder, form strip, five gamelog tabs.

The largest phase. It may split into 7a (the service and its joins) and 7b (the
page) once we start.

### Phase 8 — Team Profile
Both tabs. Mostly assembly — Phases 5 and 6 built the offensive and defensive
aggregates, and Phase 7 built the usage data.

### Phase 9 — Cheat Sheet
The column registry and the table. Cheap by this point, since every number
already exists.

### Phase 10 — Salaries *(when the files arrive)*
An adapter per site, a Mongo collection, and the salary/ownership/value columns
lighting up on the Player Profile and Cheat Sheet.

### Phase 11 — League View
Independent of everything else. Buildable any time.

**A reasonable stopping point is Phase 6** — that gives the whole Basic Plots page
working off real data, which is the piece you can use immediately.

---

# Part 5 — What to watch

**pbp memory is the thing that can go wrong.** 63 MB pruned across three seasons
is fine; 1.1 GB unpruned is not. The prune must happen in Polars *before*
`.to_pandas()`, and the column list is therefore load-bearing. If a later phase
needs a column that is not in it, the list changes and the data reloads — that is
expected, and is the cost of the choice we made. It is worth a comment on the
constant saying exactly this.

**Do not let the two halves of the app share a seasons list.** Season-long wants
six years of game logs; DFS wants three years of everything. One shared constant
would silently make one of them wrong.

**Sourcing consistency inside a difference.** Any place the page shows
`actual − expected`, both numbers come from one source. This is stated in §2.4
because it is the kind of bug that never announces itself.

**Ranks over raw values on the Team Profile.** Most defensive numbers are
meaningless without league context.

**The Cheat Sheet will be tempting to overload.** The Draft Runner console reached
14 columns and became harder to read, not easier. The column registry means every
statistic *can* be shown; the default set should stay small.

**Season-long pages must not regress.** The DFS work adds a repository, services
and pages, and touches `AppContext` and `streamlit_app.py`. Nothing in
`pages/draft_*`, `draft_model/` or the existing services should change. The
existing 387 tests are the guard, and they run on every phase.
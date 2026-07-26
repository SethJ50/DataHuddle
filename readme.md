# DataHuddle
DataHuddle is a custom web-application for fantasy football data!

A single-user app — no login or accounts. See [PLANNING.md](PLANNING.md) for the underlying data model and implementation reference.

## TODO
- Culminate all data: FFB Ratings, ADP's and Platform Ratings, ...
- Update db functions to accomodate updating / loading data
- Add loading of this data to data_manager classes
- Create some sort of overall player collection that can store player markings and notes
- Create mechanism to handle player name discrepancies between platforms
- Set up as Git repo

## Pages

Build order: **Draft Analysis** section, then **DFS** section.

---

### Draft Analysis

#### Player Profile
A single-player deep dive for draft prep.

- **Components:** player-search dropdown, player headshot, team & position, season projections (season-long and per-game fantasy points, in regular / half-PPR / full-PPR scoring), past game log
- **Markings:** Love, Like, Value, Sleeper, Hate — plus category tags (e.g. Upside Mid-Round Receiver, Uncertain Backfield)
- **Notes:** free-text notes field, saved per player
- *Markings/notes here are specific to Draft Analysis — independent from the DFS Player Profile's markings on the same player.*

#### Team Profiles
A team-level view for depth and usage analysis.

- **Filters:** Team, Year
- **Components:**
    - **Depth Chart** — rows QB1, QB2, RB1–3, WR1–5, TE1–2 (ranked by our own fantasy projections per position); columns for Fantasy Projection, ADP, marking buttons (Love/Like/Value/Sleeper/Hate), and an editable Team Notes section
    - **Player Shares** — Target Share, Rush Share, Goal Line Share, Red Zone Target/Rush Share, Deep Target Share, viewable by Week, Year, or season-to-date
    - **Game Log** — box-score style table

#### ADP Platform Comparison
Compare average draft position across platforms (ESPN, Yahoo, Sleeper).

- **Components:** two platform-select dropdowns, resulting comparison table
- **Table features:** searchable, sortable, filterable by position

#### Player Categories / Markings
Curated shortlists for specific draft situations.

- **Components:** one table per category, listing players tagged into that category
- **Categories:**
    - New Top 12 Receiver Candidate ("Upside Mid-Round Receiver")
    - Uncertain Backfields
- *Category membership is set by hand, not computed automatically.*

#### Draft Plan
A working board for planning an individual draft, with support for multiple saved plans (e.g. "2026 Home League", "2026 Best Ball #2").

- **Setup inputs:** number of teams, your draft position, draft platform
- **Components:** per core position (QB, RB, WR, TE), an editable table of picks with columns:
    - Round, Pick
    - `{Position}` Name — searchable player dropdown
    - ADP
    - True Value — the player's rank in our own fantasy projections
    - Diff — ADP rank minus True Value rank (positive = value pick, negative = reach)
    - Available % — reserved for a future predictive model, not yet implemented
- **Markings:** Safe, Upside, Late, Early
- Plans are saved and can be revisited or duplicated for a new draft.

---

### DFS

#### Player Profile
A single-player view for weekly lineup decisions.

- **Components:** player-search dropdown, player headshot, info bug (position & team), DFS salary bug (salaries for the current week), game log (most recent game first)
- **Markings:** Love, Like, Value, Cash, GPP
- **Notes:** free-text notes field, saved per player
- *Markings/notes here are specific to DFS — independent from the Draft Analysis Player Profile's markings on the same player.*

#### Team Profile
A DFS-tailored version of the team view (scope to be refined).

#### Actual FPTS vs. XFP
Scatter plot comparing actual fantasy points to expected fantasy points (XFP).

- **Filters:** stat type (Rushing, Receiving) or position (RB, WR, TE), Week Range

#### Pace of Play
Plot of neutral-script pass rate by team.

- **Filters:** Week Range

#### FPTS Per Rush vs. EPA Per Rush
Plot comparing fantasy points per rush to EPA per rush.

- **Filters:** Week Range

#### FPTS Per Pass Attempt vs. EPA Per Pass Attempt
Plot comparing fantasy points per pass attempt to EPA per pass attempt.

- **Filters:** Week Range

---

### Home
A simple landing page — app title, short description, and links to the main pages.
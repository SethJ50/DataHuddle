"""Daily Fantasy page: every player on a slate, one row each, columns you choose.

The widest view in the app: one row per player and a checklist of statistics to
show or hide. It exists because no fixed set of columns suits every question --
the numbers you want when picking a cheap tight end are not the ones you want
when deciding between two expensive backs.

TWO MODES, decided by whether salaries have been loaded for the week being looked
at. A week with prices is a SLATE that has not been played, and shows what each
player costs beside his recent form. A week without them has been played, and
shows what actually happened. The page says which it is showing.

What can be shown is declared in presentation/dfs_cheatsheet.py, so this page has
no opinion about any particular statistic.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st

from presentation.dfs_cheatsheet import (
    DEFAULT_COLUMNS, FLEX_POSITIONS, GROUPS, HISTORY_GAMES, POSITION_FILTERS,
    SLATE_ONLY_GROUPS, build, position_defaults,
)
from services.dfs_dst_service import dst_weeks
from services.dfs_player_service import player_weeks, slate
from services.dfs_salary_service import DEFAULT_TRAILING_GAMES, slate_board
from services.dfs_scoring import DfsScoring
from streamlit_state import get_app_context

ctx = get_app_context()
repo = ctx.dfs_read_repo
salaries = ctx.dfs_salary_repo

st.title("Cheat Sheet")
st.caption("Daily Fantasy")


@st.cache_data(show_spinner="Joining player data…")
def load(scoring):
    """Build the player-week table once per scoring choice."""
    return player_weeks(repo, scoring)


@st.cache_data(show_spinner="Reading salaries…")
def load_slates():
    """List which weeks have prices loaded."""
    return salaries.available_slates()


@st.cache_data(show_spinner="Scoring defences…")
def load_defences():
    """Build the weekly team-defence table, which no player table carries."""
    return dst_weeks(repo)


@st.cache_data(show_spinner="Reading salaries…")
def load_slate(season, week):
    """Read one week's prices, both sites."""
    return salaries.slate(season, week)


# ---------------------------------------------------------------------------
# Which contest, which slate
# ---------------------------------------------------------------------------
controls = st.columns([1.6, 1.1, 1.1, 2.0, 2.4])

with controls[0]:
    # The site choice drives the scoring as well as the prices, so the sheet
    # always shows one contest's salaries in that contest's own scoring. PPR is
    # here as the untouched source, and carries no prices.
    scoring = st.segmented_control(
        "Contest", list(DfsScoring), default=DfsScoring.FANDUEL,
        key="dfs_sheet_scoring", required=True,
    )
    scoring = scoring if scoring in tuple(DfsScoring) else DfsScoring.FANDUEL

frame = load(scoring)
if frame.empty:
    st.warning("No player data loaded.", icon=":material/warning:")
    st.stop()

loaded = load_slates()
priced = ({(int(row.season), int(row.week)) for row in loaded.itertuples()}
          if not loaded.empty else set())

# Seasons with stats, plus any season that has prices but no games yet -- which
# is the normal state of a season about to start.
seasons = sorted(set(frame["season"].dropna().astype(int))
                 | {season for season, _ in priced}, reverse=True)

with controls[1]:
    season = st.selectbox("Season", seasons, key="dfs_sheet_season")

season_rows = frame[frame["season"] == season]
played_weeks = sorted(int(w) for w in season_rows["week"].dropna().unique())
priced_weeks = sorted(week for s, week in priced if s == season)
weeks = sorted(set(played_weeks) | set(priced_weeks))

if not weeks:
    st.info("Nothing loaded for this season yet.", icon=":material/inbox:")
    st.stop()

with controls[2]:
    # Prefer a loaded slate, since that is the week somebody is deciding about.
    # Otherwise the most recent week that was a full slate rather than a playoff
    # round -- the last week of a finished season is the Super Bowl, two teams.
    if priced_weeks:
        default_week = priced_weeks[-1]
    else:
        playing = season_rows.groupby("week")["team"].nunique()
        full = [w for w in played_weeks if playing.get(w, 0) >= 16]
        default_week = full[-1] if full else weeks[-1]

    week = st.selectbox("Week", weeks, index=weeks.index(default_week),
                        key=f"dfs_sheet_week_{season}")

is_slate = (season, week) in priced and scoring != DfsScoring.PPR

with controls[3]:
    # ONE POSITION AT A TIME. The columns worth seeing differ completely between
    # a quarterback and a defence, so a sheet showing all of them at once shows
    # each of them badly. FLX is the exception, and exists because a flex choice
    # is a comparison ACROSS positions.
    position = st.segmented_control(
        "Position", POSITION_FILTERS, default="RB",
        key="dfs_sheet_position", required=True,
    )
    position = position if position in POSITION_FILTERS else "RB"

wanted = list(FLEX_POSITIONS) if position == "FLX" else [position]

# ---------------------------------------------------------------------------
# Build the board, one way or the other
# ---------------------------------------------------------------------------
if is_slate:
    with controls[4]:
        form_games = st.slider("Form window (games)", 3, 10,
                               DEFAULT_TRAILING_GAMES, key="dfs_sheet_form")

    board = slate_board(load_slate(season, week), frame, season, week,
                        str(scoring), games=form_games,
                        defences=load_defences(), repo=repo, scoring=scoring,
                        history=HISTORY_GAMES)
    board = board[board["position"].isin(wanted)]

    crossed = (board["form_seasons_back"].fillna(0).max()
               if not board.empty and "form_seasons_back" in board else 0)
    st.info(
        f"**Slate — not yet played.** {scoring} prices for week {week}, "
        f"{season}, beside each player's last {form_games} games."
        + (" Those games reach back into a previous season for most players, so "
           "a role that changed over the offseason will not show here yet."
           if crossed else ""),
        icon=":material/schedule:",
    )
    defaults = position_defaults(position)
else:
    with controls[4]:
        minimum_snaps = st.slider("Minimum snaps", 0, 40, 8,
                                  key="dfs_sheet_snaps",
                                  help="Hides players who barely appeared.")

    board = slate(frame, season, week, positions=wanted,
                  minimum_snaps=minimum_snaps)
    if (season, week) in priced and scoring == DfsScoring.PPR:
        st.caption("Prices exist for this week, but PPR is not a contest — "
                   "pick FanDuel or DraftKings to see them.")
    defaults = DEFAULT_COLUMNS

# ---------------------------------------------------------------------------
# Which columns
# ---------------------------------------------------------------------------
# The slate-only groups all describe trailing form or a fixture, which a week
# that has already been played answers with what actually happened instead.
groups = {name: columns for name, columns in GROUPS.items()
          if is_slate or name not in SLATE_ONLY_GROUPS}

with st.expander("Columns", expanded=False):
    st.caption("Player, position, team and opponent are always shown. "
               "Everything else is optional — the default set is deliberately "
               "small, because more numbers per row helps you hesitate rather "
               "than decide.")

    chosen = []
    for (name, columns), cell in zip(groups.items(), st.columns(len(groups))):
        with cell:
            st.markdown(f"**{name}**")
            for column in columns:
                # The key carries the mode, so each mode keeps its own ticks --
                # a slate's sensible default set is not a played week's.
                # The key carries the mode AND the position, so each keeps its
                # own ticks -- a back's sensible default set is not a defence's.
                if st.checkbox(column.label, value=column.field in defaults,
                               key=f"dfs_sheet_{is_slate}_{position}_{column.field}",
                               help=column.help or None):
                    chosen.append(column.field)

# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
if board.empty:
    st.info("Nothing matches those filters.", icon=":material/filter_alt_off:")
    st.stop()

table, columns = build(board, chosen)

st.dataframe(
    table, hide_index=True, width="stretch", height=620,
    column_config={
        column.field: (
            st.column_config.NumberColumn(column.label, format=column.format,
                                          help=column.help or None)
            if column.format else
            st.column_config.TextColumn(column.label, help=column.help or None)
        )
        for column in columns
    },
)

footnote = (f"{len(table)} players · week {week}, {season} · {scoring} scoring. "
            "Click a heading to sort. Blank cells are missing data, not zeroes.")
if is_slate and scoring == DfsScoring.DRAFTKINGS:
    # Stated where the numbers are, not buried in a docstring.
    footnote += (" DraftKings' 100- and 300-yard bonuses count towards points "
                 "scored but not towards expected points, so xFP reads a little "
                 "low for high-yardage players.")
st.caption(footnote)

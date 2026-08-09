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

import itertools

import streamlit as st

from presentation.dfs_cheatsheet import (
    DEFAULT_COLUMN_WIDTH, DEFAULT_COLUMNS, FLEX_POSITIONS, GROUPS,
    HISTORY_GAMES, POSITION_FILTERS, SLATE_ONLY_GROUPS, build, every_column,
    position_defaults, scale_directions,
)
from presentation.st_tables import color_scale
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
# Settings on the left, the column picker on the right. Both containers are
# created up front and written into further down, because several of these
# controls cannot be drawn until data loaded by an earlier one is in hand -- the
# week list needs the season, and the season list needs the contest.
settings_col, picker_col = st.columns([3, 2])

with settings_col:
    contest_row = st.columns([1, 1.4])     # Contest | Position
    slate_row = st.columns(3)              # Season | Week | Form window

with contest_row[0]:
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

with slate_row[0]:
    season = st.selectbox("Season", seasons, key="dfs_sheet_season")

season_rows = frame[frame["season"] == season]
played_weeks = sorted(int(w) for w in season_rows["week"].dropna().unique())
priced_weeks = sorted(week for s, week in priced if s == season)
weeks = sorted(set(played_weeks) | set(priced_weeks))

if not weeks:
    st.info("Nothing loaded for this season yet.", icon=":material/inbox:")
    st.stop()

with slate_row[1]:
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

with contest_row[1]:
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
    with slate_row[2]:
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
    with slate_row[2]:
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
# The two halves of the catalogue are EXCLUSIVE, not nested. A slate offers the
# trailing-form groups and the fixture; a week already played offers what
# actually happened. Neither mode's columns exist in the other's table, so
# showing both would offer checkboxes that tick and change nothing -- and since
# the two halves measure the same statistics, they read as duplicates.
groups = {name: columns for name, columns in GROUPS.items()
          if (name in SLATE_ONLY_GROUPS) == is_slate}

# WHICH COLUMNS ARE ON IS KEPT HERE, not read back off the checkboxes.
#
# Only one group's checkboxes are drawn at a time, and Streamlit DISCARDS the
# state of any keyed widget it did not render on the last run. Collecting
# `chosen` from the boxes on screen -- which is what this page used to do -- would
# therefore forget every other group's ticks the moment you changed category.
#
# One store per mode and position, since a slate's sensible set is not a played
# week's and a back's is not a defence's. Seeded from that combination's own
# defaults the first time it is seen.
selection_key = f"dfs_sheet_cols_{is_slate}_{position}"
if selection_key not in st.session_state:
    st.session_state[selection_key] = set(defaults)
selected = st.session_state[selection_key]


def group_label(name):
    """Name a group, and say how many of its columns are switched on.

    The cost of showing one group at a time is that the others' ticks are out of
    sight. Putting the count in the dropdown is what stops the picker feeling
    like it has forgotten them.

    Steps:
        1. Count how many of that group's fields are in the current selection.
        2. Append the count when there is one, and nothing when there is not, so
           the untouched groups stay quiet.

    Args:
        name: The group's name, as it appears in GROUPS.

    Returns:
        str: Something like "Receiving (opps) · 3".
    """
    on = sum(1 for column in groups[name] if column.field in selected)
    return f"{name} · {on}" if on else name


# Drawn into the right-hand container reserved at the top of the page, so the
# picker sits BESIDE the settings rather than under them -- and is narrower for
# it, which is the point.
picker = picker_col.expander(f"Columns · {len(selected)} on", expanded=False)
with picker:
    header = st.columns([3, 2], vertical_alignment="bottom")

    with header[0]:
        group_name = st.selectbox("Category", list(groups),
                                  format_func=group_label,
                                  key=f"dfs_sheet_group_{is_slate}_{position}")
    with header[1]:
        if st.button("Reset", width="stretch",
                     help="Back to this position's default columns."):
            st.session_state[selection_key] = set(defaults)
            st.rerun()

    st.caption("Player, position, team and opponent are always shown.")

    # Three across, so a thirteen-column group is four short rows rather than one
    # long one. The checkbox key carries the field, so switching category swaps
    # the whole set of widgets rather than reusing them under new labels.
    for column, cell in zip(groups[group_name],
                            itertools.cycle(st.columns(3))):
        with cell:
            ticked = st.checkbox(
                column.label, value=column.field in selected,
                key=f"dfs_sheet_{is_slate}_{position}_{column.field}",
                help=column.help or None,
            )
        # Written straight back to the store, which is the only record of it.
        if ticked:
            selected.add(column.field)
        else:
            selected.discard(column.field)

# Ordered by the catalogue rather than by when each was ticked, so the table's
# shape stays familiar however somebody arrives at it.
chosen = [column.field for column in every_column() if column.field in selected]

# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
if board.empty:
    st.info("Nothing matches those filters.", icon=":material/filter_alt_off:")
    st.stop()

table, columns = build(board, chosen)

# Blue for the good end of each column, red for the bad, nothing in the middle.
# Ranked WITHIN THE ROWS ON SCREEN, so the colours answer "compared with the
# other players I am choosing between" rather than "compared with the league".
styled = table.style.apply(color_scale(scale_directions(table, columns)),
                           axis=None)

# KEYED BY LABEL, not by field. `build` gives the table two-level headings, and
# Streamlit names such a column after the LEAF of the pair -- so the label is
# what a config entry has to match. That is also why every label in a group set
# has to be unique; see the test that enforces it.
#
# No label is passed to the column itself: the heading already comes from the
# MultiIndex, and passing one here would override the group's leaf with the same
# text to no effect.
st.dataframe(
    styled, hide_index=True, width="stretch", height=620,
    column_config={
        column.label: (
            st.column_config.NumberColumn(
                format=column.format, help=column.help or None,
                width=column.width if column.width is not None
                else DEFAULT_COLUMN_WIDTH)
            if column.format else
            st.column_config.TextColumn(
                help=column.help or None,
                width=column.width if column.width is not None
                else DEFAULT_COLUMN_WIDTH)
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

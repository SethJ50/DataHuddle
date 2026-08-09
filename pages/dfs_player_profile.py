"""Daily Fantasy page: one player, his price, his form, and his game log.

The Daily Fantasy counterpart to pages/player_profile.py. That page is built for
drafting a player for a season; this one is built for buying him for a single
week, so it leads with salary and recent form rather than notes and tags.

Three columns. The narrow left one identifies the player, holds what he costs
and carries every control; the middle one summarises his recent form against his
position; the wide right one holds the game logs.

EVERYTHING LOOKS BACKWARDS FROM A TARGET WEEK, which is normally one nobody has
played yet -- so the window reaches into the previous season rather than stopping
at a season boundary. In week 1 of a new year, "his last five games" means his
last five of the year before.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import pandas as pd
import streamlit as st

from presentation.colors import SITE_COLORS, badge_html, position_badge_html
from presentation.dfs_gamelog import (percentile_frame, selectable_stats,
                                      shape, tabs_for)
from presentation.st_tables import shade_by_percentile, shade_cells
from services.dfs_player_service import (last_games, played_before,
                                         player_weeks, upcoming_week,
                                         weekly_percentiles, window_summary)
from services.dfs_scoring import DfsScoring
from streamlit_state import get_app_context
from presentation.dfs_charts import trend_chart

DEFAULT_FORM_GAMES = 5

SITES = (DfsScoring.FANDUEL, DfsScoring.DRAFTKINGS)

ctx = get_app_context()
repo = ctx.dfs_read_repo
salaries = ctx.dfs_salary_repo


@st.cache_data(show_spinner="Reading salaries…")
def load_prices():
    """Read the most recent slate's prices, both sites.

    Steps:
        1. Ask which weeks have prices loaded.
        2. Read the newest of them.

    Returns:
        tuple: `(frame, season, week)`, or `(None, None, None)` if no slate has
            ever been loaded.
    """
    loaded = salaries.available_slates()
    if loaded.empty:
        return None, None, None

    newest = loaded.iloc[0]
    season, week = int(newest["season"]), int(newest["week"])
    return salaries.slate(season, week), season, week


@st.cache_data(show_spinner="Joining player data…")
def load(scoring):
    """Build the player-week table once per scoring choice.

    Six sources get joined to make this, which is slow enough to be worth
    caching and small enough to keep. Cached on the scoring alone, because
    nothing else about it varies.

    Args:
        scoring: Which scoring system the points columns should be in.

    Returns:
        pd.DataFrame: The table from `player_weeks`.
    """
    return player_weeks(repo, scoring)

def player_meta_html(position, team):
    """Build the position badge and team line shown under the dropdown.

    Steps:
        1. Build the coloured position pill with `position_badge_html` from
           presentation/colors.py.
        2. Set the team beside it in quieter text.

    Args:
        position: His position, such as "RB".
        team: His team's abbreviation.

    Returns:
        str: HTML for `st.markdown(..., unsafe_allow_html=True)`.
    """
    return (f'<div style="margin:-0.35rem 0 0.6rem 0;text-align:center">'
            f'{position_badge_html(position)}'
            f'<span style="margin-left:0.45rem;font-size:0.85rem;opacity:0.75">'
            f'{team}</span></div>')


def salary_row_html(price_by_site):
    """Build the row of site price tags shown under the player's name.

    What he costs is the first question a Daily Fantasy page has to answer, so
    the two prices sit together and larger than anything else in the column.

    Steps:
        1. Walk the two sites in a fixed order, so the row never reorders itself
           between players.
        2. Format each price, using a dash where that site has not listed him —
           an absent tag would read as a rendering gap rather than a fact.
        3. Build a larger pill per site with `badge_html` from
           presentation/colors.py, and centre the pair.

    Args:
        price_by_site: Maps a site name to his salary there. A site missing from
            it, or holding a blank, is shown as a dash.

    Returns:
        str: HTML for `st.markdown(..., unsafe_allow_html=True)`.
    """
    tags = []
    for site, color in SITE_COLORS.items():
        salary = price_by_site.get(site)
        cost = f"${salary:,.0f}" if pd.notna(salary) else "—"
        tags.append(badge_html(f"{site}  {cost}", color,
                               font_size="0.95rem", padding="0.25rem 0.7rem"))

    return ('<div style="display:flex;gap:0.5rem;justify-content:center;'
            'margin:0.2rem 0 0.8rem 0">' + "".join(tags) + "</div>")


# ---------------------------------------------------------------------------
# Which player, which week, and in whose scoring
# ---------------------------------------------------------------------------
left, summary, right = st.columns([3, 3, 6])

# EVERY SLOT IS RESERVED UP FRONT and filled further down. The site control has
# to be READ before the player table can be built -- it decides the scoring every
# number on this page is expressed in -- but it belongs visually below the
# prices, and reserving its place is what lets both be true.
with left:
    with st.container(border=True):
        image_slot = st.empty()
        name_slot = st.container()
        meta_slot = st.container()
        salary_slot = st.container()

    with st.expander("Settings", expanded=False):
        site_slot = st.container()
        when_slot = st.container()
        window_slot = st.container()

with site_slot:
    scoring = st.segmented_control(
        "Site", SITES, default=DfsScoring.FANDUEL,
        key="dfs_profile_site", required=True,
    )
    scoring = scoring if scoring in SITES else DfsScoring.FANDUEL

with window_slot:
    form_games = st.number_input(
        "Last N games", min_value=2, max_value=17, value=DEFAULT_FORM_GAMES,
        key="dfs_profile_window",
        help="How many recent games the trend plot and the summary averages "
             "cover. The game log shows a whole season instead.",
    )

# Read here rather than beside the tags below, because the loaded slate is what
# decides the week the rest of the page is built for.
prices, price_season, price_week = load_prices()

frame = load(scoring)
if frame.empty:
    st.warning("No player data loaded.", icon=":material/warning:")
    st.stop()

# WHICH WEEK YOU ARE BUILDING FOR -- normally one nobody has played yet, which
# is why the form window looks strictly BACKWARDS from it and reaches into the
# previous season to do so.
#
# NOT A CONTROL. The slate you have prices for IS the week you are building for,
# so making it adjustable only invites the page to disagree with the salaries
# beside it. Falls back to a guess from the games played when nothing is loaded.
season, week = upcoming_week(frame)
if price_season is not None:
    season, week = price_season, price_week

# WHICH SEASON THE GAME LOG SHOWS, which is a different question. The target
# week is usually in a season that has not started -- 2026 has salaries but no
# games -- so defaulting the log to it would show an empty table. The newest
# season that actually has games is the useful answer.
played_seasons = sorted({int(s) for s in frame["season"].dropna()}, reverse=True)
log_default = next((s for s in played_seasons if s <= season),
                   played_seasons[0])

with when_slot:
    log_season = st.selectbox(
        "Game log season", played_seasons,
        index=played_seasons.index(log_default),
        key="dfs_profile_log_season",
        help=f"The log shows every game of the season chosen here. Everything "
             f"else on the page describes the slate you are building for — "
             f"week {week}, {season}.",
    )

# Every player's recent games, ending before the target week. One frame, reused
# by the dropdown, the game log, the trend plot and the summary column.
history = played_before(frame, season, week)
if history.empty:
    st.warning(f"No games recorded before week {week}, {season}.",
               icon=":material/warning:")
    st.stop()

# Ordered by how much each player has actually played lately, so the names worth
# looking at are near the top of a dropdown holding a few thousand of them.
busiest = (last_games(history, form_games)
           .groupby(["canonical_id", "name"], as_index=False)
           .agg(snaps=("offense_snaps", "sum"))
           .sort_values("snaps", ascending=False))
names = list(busiest["name"])
id_by_name = dict(zip(busiest["name"], busiest["canonical_id"]))

with name_slot:
    chosen_name = st.selectbox("Player", names, index=0,
                               label_visibility="collapsed",
                               key="dfs_profile_player")

canonical_id = id_by_name[chosen_name]

# TWO FRAMES, because two questions are being asked.
#
# `recent` is his last few games before the slate, wherever they fall -- what
# the trend plot and the summary column describe. It crosses the season boundary
# on purpose: in week 1 of a new year, his recent form is last year's.
recent = last_games(history[history["canonical_id"] == canonical_id],
                    form_games).sort_values("_when")

# `log_rows` is every game he played in the season chosen for the log. A whole
# season rather than a window, because the log is the record rather than a
# summary of it.
log_rows = history[(history["canonical_id"] == canonical_id)
                   & (history["season"] == log_season)]

latest = recent.iloc[-1]

headshots = recent["headshot_url"].dropna()
with image_slot.container(horizontal_alignment="center"):
    if not headshots.empty:
        st.image(headshots.iloc[-1], width=220)

with meta_slot:
    st.markdown(player_meta_html(latest["position"], latest["team"]),
                unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# What he costs on the most recently loaded slate
# ---------------------------------------------------------------------------
with salary_slot:
    if prices is None:
        st.caption("No slate loaded. Run scripts/load_salaries.py after "
                   "downloading the exports.")
    else:
        his = prices[prices["canonical_id"] == canonical_id]

        # Both tags always. An empty mapping dashes them both, so a player who
        # is not on the slate needs no branch of its own -- and a missing tag
        # would read as a rendering gap rather than as "no price".
        st.markdown(salary_row_html(dict(zip(his["site"], his["salary"]))),
                    unsafe_allow_html=True)
        st.caption(f"Week {price_week}, {price_season}"
                   + ("" if not his.empty else " — not priced this week"))

        flags = {row.injury_status for row in his.itertuples()
                 if isinstance(row.injury_status, str) and row.injury_status}
        if flags:
            st.warning(f"Injury flag: {', '.join(sorted(flags))}",
                       icon=":material/medical_services:")

with left:
    with st.container(border=True):
        st.markdown("**Trend Plot**")

        # Everything his logs carry, deduplicated -- several stats appear in more
        # than one tab and are the same column each time.
        choices = selectable_stats(latest["position"])
        picked = st.multiselect(
            "Stats", list(choices), format_func=lambda field: choices[field],
            default=[f for f in ("total_fantasy_points", "total_fantasy_points_exp")
                    if f in choices],
            key=f"dfs_trend_{canonical_id}",
            help="Each stat is shown as his percentile among players at his "
                "position that week, so any two can share the axis.",
        )

        # RANKED AGAINST THE WHOLE POSITION, so this reads `history` rather than
        # `mine` -- a percentile needs the field he is being compared with.
        trend = weekly_percentiles(history, canonical_id, picked,
                                recent["_when"], minimum_snaps=8)

        if trend.empty:
            st.caption("Pick a stat to plot." if not picked
                    else "Nothing recorded for those weeks.")
        else:
            trend["label"] = trend["stat"].map(choices)
            st.altair_chart(trend_chart(trend), width="stretch", theme=None)

with summary:
    with st.container(border=True):
        st.markdown("**Summary**")

        category = st.selectbox("Category", list(tabs_for(latest["position"])),
                                key="dfs_summary_category")

        st.caption(f"Averages over his last {form_games} games, and where each "
                f"places him among {latest['position']}s playing regularly.")

        # One small table per sub-category -- for a back the Opportunities category
        # runs to 23 stats, and the Rushing / Receiving break is what makes that
        # navigable in a narrow column.
        for sub_category, columns in tabs_for(latest["position"])[category]:
            label_of = {column.field: column.label for column in columns}

            # RANKED AGAINST THE WHOLE POSITION, so this reads `history` rather
            # than `mine` -- a percentile needs the field he is compared with.
            standing = window_summary(history, canonical_id, list(label_of),
                                    season, week, games=form_games)
            if standing.empty:
                continue

            table = pd.DataFrame({
                "Stat": standing["stat"].map(label_of),
                "Avg": standing["average"],
                "%ile": standing["percentile"],
            })

            st.caption(sub_category)
            st.dataframe(
                table.style.apply(
                    shade_by_percentile("Avg", standing["percentile"]), axis=None),
                hide_index=True, width="stretch",
                column_config={
                    "Stat": st.column_config.TextColumn("Stat", width=90),
                    "Avg": st.column_config.NumberColumn("Avg", width=70,
                                                        format="%.2f"),
                    "%ile": st.column_config.NumberColumn(
                        "%ile", width=60, format="%.0f",
                        help="Percentile among players at his position averaging "
                            "at least 10 snaps a game over the same weeks."),
                },
            )

            # A stat with an average but no percentile has no better end -- see
            # UNRANKED in presentation/dfs_gamelog.py. Named here so the gap reads
            # as a decision rather than as a calculation that fell over. Stats he
            # has no data for at all were dropped upstream and never reach this.
            unranked = [label_of[field] for field in
                        standing.loc[standing["percentile"].isna(), "stat"]]
            if unranked:
                st.caption(f":gray[{', '.join(unranked)} — no percentile: neither "
                        f"end of these is better.]")

with right:
    with st.container(border=True):
        # ---------------------------------------------------------------------
        # The game logs, split by the question each answers
        # ---------------------------------------------------------------------
        st.caption(f"Every {log_season} game — {len(log_rows)} of them. Cells are coloured by how that week ranked among {latest['position']}s.")

        tabs = tabs_for(latest["position"])
        for tab, (name, groups) in zip(st.tabs(list(tabs)), tabs.items()):
            with tab:
                table, present = shape(log_rows, groups)
                if table.empty or not present:
                    st.caption("Nothing recorded for this view.")
                    continue

                # Each CELL is coloured by how that week ranked against everybody
                # else at his position THAT WEEK -- not against the other rows on
                # screen, which are far too few to rank within and would only say
                # "his best of these five".
                ranks = weekly_percentiles(history, canonical_id,
                                        [column.field for column in present],
                                        log_rows["_when"], minimum_snaps=8)
                shading = percentile_frame(
                    table, log_rows.loc[table.index, "_when"], ranks, present)

                # Keyed by LABEL, not field: the headings are two-level now, and
                # Streamlit names such a column after its leaf.
                st.dataframe(
                    table.style.apply(shade_cells(shading), axis=None),
                    hide_index=True, width="stretch", height=430,
                    column_config={
                        column.label: st.column_config.NumberColumn(
                            format=column.format, help=column.help or None,
                            width=column.width)
                        if column.format else st.column_config.TextColumn(
                            help=column.help or None, width=column.width)
                        for column in present
                    },
                )

        st.caption("Blank cells are missing data, not zeroes. Tracking and charting "
                "numbers only cover players above their sources' volume "
                "thresholds, so a quiet week often has none.")

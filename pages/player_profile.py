"""Page showing one player: his photo, your notes about him, and his game log.

Two halves. The narrow left column identifies the player and holds the tags and
notes you have written about him, which are saved per draft so the same player
can be a target in one league and a fade in another. The wide right column shows
his week-by-week stats, with the columns chosen to suit his position.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""
import numpy as np
import pandas as pd
import streamlit as st

from streamlit_state import get_app_context
from scoring import ScoringFormat
from draft_model.queries import adjust_within_position
from presentation.colors import position_badge_html
from presentation.gamelog_view import add_fantasy_points, columns_for, shape
from ui_helpers import draft_selector, adp_to_round_pick, load_sim_board, load_platform_adp
from registry import MARKING_CATEGORIES
from presentation.marks import mark_help, mark_emoji
from presentation.projection_stats import ALL_STAT_COLUMNS, stat_groups, stat_label

ctx = get_app_context()

@st.cache_data(show_spinner="Loading players...")
def load_player_index():
    """Load every in-scope player with the details this page displays.

    The dropdown needs names, and the line under it needs position, team and bye
    week. Both come from the same roster call, so it is made once here rather
    than twice.

    Caching matters more than it looks: `roster()` re-resolves every UDK name to
    a canonical id each time it is called, and Streamlit re-runs this whole file
    on every click.

    Steps:
        1. Ask the roster service for the app's player universe.
        2. Keep only the columns this page uses, dropping the headshot, ranks and
           ratings it does not.

    Returns:
        pd.DataFrame: One row per in-scope player, with `canonical_id`,
            `display_name`, `position`, `team`, and `bye_week`. One row per
            player — the roster service has already dropped duplicates — so this
            is safe to index by `canonical_id`.
    """
    return ctx.roster_service.roster()[
        ["canonical_id", "display_name", "position", "team", "bye_week"]
    ]

@st.cache_data(show_spinner="Scoring players...")
def load_player_stats(fmt_value):
    """Build every player's projection, position rank, and adjusted ratings.

    The value half of the stats panel. None of it is draft-scoped — only the
    scoring format changes it — so it is computed for the whole player pool once
    and looked up one row at a time.

    Steps:
        1. Take risk and upside from the roster service, and the blended
           projection from the projections service, and join them by player.
        2. Rank each player against others AT HIS OWN POSITION by projected
           points, which is what turns a points total into "QB3".
        3. Adjust risk and upside with `adjust_within_position` from
           draft_model/queries.py, which strips out the part of each rating that
           merely restates the projection.

    Args:
        fmt_value: The scoring format as its stored string, such as "half_ppr".
            Also the cache key, since every number here depends on it.

    Returns:
        pd.DataFrame indexed by `canonical_id`, with `position`, `projection`
            (season points), `pos_rank` (1 is the best at that position),
            `risk_adj` and `upside_adj`. The two adjusted ratings are centred on
            zero WITHIN each position, so 0 means "typical for his position",
            positive upside means more explosive than his projection suggests,
            and positive risk means shakier than it suggests. NaN for anyone
            unrated or unprojected.

    Note:
        THE RATINGS ARE ADJUSTED AGAINST THE WHOLE POOL, which is what
        `adjust_within_position` asks for. Raw UDK upside tracks projected points
        so closely that a bar of it would just be a blurrier copy of the
        projection sitting beside it; the adjustment is what makes it say
        something the rest of the panel does not.
    """
    # 1 row per in-scope player -- canonical_id, position, risk, upside.
    roster = ctx.roster_service.roster()[["canonical_id", "position", "risk", "upside"]]

    # 1 row per projected player -- canonical_id, a points column per format, and
    # the ten blended stat columns. `_blend` averages EVERY numeric column, so the
    # raw stats survive the three-analyst blend alongside the points.
    points_col = f"fantasy_points_{fmt_value}_season"
    projected = ctx.projections_service.get_own_projections()

    # Intersected rather than indexed directly, so a stat the adapter stops
    # publishing leaves a blank row instead of raising on every page load.
    stat_columns = [c for c in ALL_STAT_COLUMNS if c in projected.columns]
    projections = projected[["canonical_id", points_col, *stat_columns]]

    stats = roster.merge(projections, on="canonical_id", how="left").rename(
        columns={points_col: "projection"}
    )

    # method="min" so two tied players share the better rank rather than being
    # separated arbitrarily -- the same convention the rest of the app uses.
    stats["pos_rank"] = stats.groupby("position")["projection"].rank(
        method="min", ascending=False
    )

    for rating in ("risk", "upside"):
        stats[f"{rating}_adj"] = adjust_within_position(
            stats[rating].to_numpy(),
            stats["projection"].to_numpy(),
            stats["position"].to_numpy(),
        )

    return stats.set_index("canonical_id")


def player_meta_html(row):
    """Build the position badge and team / bye line shown under the dropdown.

    Identifies the player at a glance, so you can tell immediately that you are
    looking at the wrong Josh Allen.

    Steps:
        1. Build the coloured position pill with `position_badge_html` from
           presentation/colors.py.
        2. Add the team abbreviation, if there is one.
        3. Add the bye week, converting through `float` first so it works whether
           the source stored it as a number or as text, and skipping it entirely
           when it is missing or unreadable.
        4. Join the text parts with a middle dot and set them beside the badge.

    Args:
        row: One row of `load_player_index`'s table, holding `position`, `team`,
            and `bye_week`.

    Returns:
        str: HTML for `st.markdown(..., unsafe_allow_html=True)`. Just the badge
            when neither team nor bye week is known.
    """
    parts = []
    if pd.notna(row["team"]):
        parts.append(str(row["team"]))

    # Defensive: UDK's "Bye Week" arrives as a number for most players but can be
    # blank or non-numeric. An unreadable bye is dropped, not shown as garbage.
    try:
        parts.append(f"Bye {int(float(row['bye_week']))}")
    except (TypeError, ValueError):
        pass

    text = " · ".join(parts)
    return (
        f'<div style="margin:-0.35rem 0 0.6rem 0;text-align:center">'
        f'{position_badge_html(row["position"])}'
        f'<span style="margin-left:0.45rem;font-size:0.85rem;opacity:0.75">{text}</span>'
        f"</div>"
    )

def stat_row(label, value, help=None):
    """Draw one label-on-the-left, value-on-the-right line.

    The same layout the marking rows use, so the whole left side of the page
    reads as one list rather than two different ones.

    Steps:
        1. Open a horizontal container that pushes its first and last child to
           opposite ends.
        2. Write the label, carrying the tooltip if there is one.
        3. Write the value in bold, so the numbers are what your eye lands on.

    Args:
        label: The stat's name, such as "SimAdp".
        value: Already-formatted text. Pass "—" for a value that is unavailable.
        help: Optional hover text explaining what the number means.

    Returns:
        None: Written straight to the page.
    """
    with st.container(horizontal=True, vertical_alignment="center",
                      horizontal_alignment="distribute"):
        st.markdown(label, help=help)
        st.markdown(f"**{value}**")


def rating_bar_html(value, scale, good="high"):
    """Draw a bar that grows left or right from a centre line.

    Risk and upside are adjusted to sit around zero, so "how big is it" is only
    half the answer — the direction is the other half. A bar that fills from the
    left could not show that, so this one starts in the middle and grows either
    way.

    Steps:
        1. Return a bare track for a player with no rating, rather than a bar of
           length zero, which would claim he is exactly average.
        2. Work out how far to fill, capping at half the track so an outlier
           cannot overflow.
        3. Choose the side from the sign, and the colour from whether that sign
           is the good direction for this rating.
        4. Build the track with the fill positioned against the centre.

    Args:
        value: The adjusted rating. Zero means typical for his position.
        scale: The value that fills half the track completely. Anything larger
            is clipped.
        good: "high" when a bigger number is better (upside), "low" when a
            smaller one is (risk, where lower is safer).

    Returns:
        str: HTML for `st.markdown(..., unsafe_allow_html=True)`.
    """
    track = ('<div style="position:relative;height:0.5rem;border-radius:3px;'
             'background:rgba(128,128,128,0.18)">'
             '<div style="position:absolute;left:50%;top:0;bottom:0;width:1px;'
             'background:rgba(128,128,128,0.55)"></div>{fill}</div>')

    if value is None or not np.isfinite(value) or not scale:
        return track.format(fill="")

    # Half the track is the full extent in either direction.
    width = min(abs(value) / scale, 1.0) * 50
    is_good = (value > 0) if good == "high" else (value < 0)
    colour = "#22c55e" if is_good else "#e8833a"

    side = "left:50%" if value > 0 else f"right:50%"
    return track.format(
        fill=f'<div style="position:absolute;{side};top:0;bottom:0;'
             f'width:{width:.1f}%;background:{colour};border-radius:2px"></div>'
    )

# DataFrame: 1 row per in-scope player -- canonical_id, display_name, position,
# team, bye_week.
players = load_player_index()

# The dropdown shows names but the rest of the app works in ids, so both
# directions of the mapping are needed.
names_by_id = dict(zip(players["canonical_id"], players["display_name"]))
id_by_name = {name: cid for cid, name in names_by_id.items()}
sorted_names = sorted(id_by_name)

# Indexed for the one-row lookup the badge line does each rerun.
details_by_id = players.set_index("canonical_id")


with st.sidebar:
    st.header("Draft")
    # Markings are scoped to a draft, so this page needs a draft selected too.
    draft = draft_selector(ctx, "player_profile")

# Which scoring format everything on this page is expressed in. Read from the
# draft rather than a control; Half PPR without one, as elsewhere in the app.
fmt_value = draft["scoring_format"] if draft else ScoringFormat.HALF_PPR.value

player_col, stat_col, right = st.columns([3, 3, 6])

with player_col:
    image_slot = st.empty()

    selected_name = st.selectbox(
        "Player", sorted_names, index=0, label_visibility="collapsed"
    )
    canonical_id = id_by_name[selected_name]

    headshot_url = ctx.player_directory.get_headshot(canonical_id)

    with image_slot.container(horizontal_alignment="center"):
        st.image(headshot_url, width=200)

    # Appears directly under the dropdown: `image_slot` was reserved above it,
    # so the headshot fills that gap no matter when it is written.
    st.markdown(player_meta_html(details_by_id.loc[canonical_id]),
                unsafe_allow_html=True)
    
    with st.container(border=True):
        st.markdown("**Tags + Notes:**")
        if draft is None:
            st.info("Select a draft in the sidebar to add markings.")
        else:
            saved = ctx.player_markings_service.get(draft["draft_id"], canonical_id)
            saved_categories = set(saved["categories"])

            # One row per marking: full name, its emoji if it has one, one help
            # icon, and the box pushed to the far right.
            #
            # `horizontal=True` lays the row out as flexbox and
            # `horizontal_alignment="distribute"` puts the first and last child at
            # opposite ends -- which is what keeps the box right-aligned without
            # guessing at column ratios. `st.checkbox` is already width="content",
            # so it shrinks to the box rather than filling the row.
            chosen = []
            for category in MARKING_CATEGORIES:
                with st.container(horizontal=True, vertical_alignment="center",
                                  horizontal_alignment="distribute"):
                    # The ONLY help= in the row. Putting one on each element is
                    # what produced two "?" icons.
                    st.markdown(f"{category} {mark_emoji(category)}".rstrip(),
                                help=mark_help(category))

                    # The real category is passed as the label and then hidden: a
                    # screen reader still announces which box this is, and
                    # Streamlit does not warn about an empty label.
                    #
                    # The key includes draft + player, so switching either starts
                    # the boxes from THAT player's saved marks -- `value=` only
                    # applies when the key is new.
                    ticked = st.checkbox(
                        category,
                        value=category in saved_categories,
                        key=f"mark_{draft['draft_id']}_{canonical_id}_{category}",
                        label_visibility="collapsed",
                    )
                if ticked:
                    chosen.append(category)

            notes = st.text_area(
                "Notes",
                value=saved["notes"],
                key=f"notes_{draft['draft_id']}_{canonical_id}",
            )

            if st.button("Save"):
                ctx.player_markings_service.save(
                    draft["draft_id"], canonical_id, chosen, notes
                )
                st.toast("Saved")

with stat_col:
    # DataFrame indexed by canonical_id -- position, projection, pos_rank,
    # risk_adj, upside_adj.
    stats = load_player_stats(fmt_value)
    row = stats.loc[canonical_id] if canonical_id in stats.index else None

    # --- draft position: where the market and the model expect him to go -----
    plat_adp = sim_adp = sim_stdev = None
    if draft is not None:
        plat_adp = load_platform_adp(ctx, draft["platform"], fmt_value).get(canonical_id)

        sim_board, sim_error = load_sim_board(ctx, draft, year=2026)
        if not sim_error:
            # Row order IS picks-matrix column order, so one lookup serves both.
            matches = sim_board.table.index[
                sim_board.table["canonical_id"] == canonical_id
            ]
            if len(matches):
                sim_adp = sim_board.simulated_adp[matches[0]]
                sim_stdev = sim_board.simulated_stdev[matches[0]]

    def as_slot(value):
        """Format one pick number as ROUND.PICK text, or a dash when missing."""
        if value is None or not np.isfinite(value) or draft is None:
            return "—"
        return f"{adp_to_round_pick(value, draft['num_teams']):.2f}"

    with st.container(border=True):
        st.markdown("**Draft Stats:**")
        stat_row("PlatAdp", as_slot(plat_adp),
                 help="Where your league's platform has him going, as ROUND.PICK.")
        stat_row("SimAdp", as_slot(sim_adp),
                 help="The average pick he went at across the simulated drafts.")

        # SimAdp plus or minus one simulated standard deviation -- the band he
        # lands in in roughly two drafts out of three.
        if sim_adp is not None and sim_stdev is not None and np.isfinite(sim_stdev):
            draft_range = f"{as_slot(sim_adp - sim_stdev)} – {as_slot(sim_adp + sim_stdev)}"
        else:
            draft_range = "—"
        stat_row("Draft Range", draft_range,
                 help="SimAdp give or take one simulated standard deviation — "
                      "where he goes in about two drafts out of three.")

    # --- value: what he is worth, and how he compares at his position --------
    with st.container(border=True, height=550):
        st.markdown("**Projections**")

        projection = row["projection"] if row is not None else np.nan
        stat_row("Projection",
                 f"{projection:.0f}" if pd.notna(projection) else "—",
                 help="Fantasy Footballers blended projection, season points in "
                      "this draft's scoring format.")

        pos_rank = row["pos_rank"] if row is not None else np.nan
        stat_row("PosRank",
                 f"{row['position']}{int(pos_rank)}" if pd.notna(pos_rank) else "—",
                 help="His rank at his own position by projected points.")

        # Projected season stat line, in whichever groups suit his position. A
        # position with no groups -- or no player resolved yet -- renders nothing.
        position = row["position"] if row is not None else None
        for heading, columns in stat_groups(position):
            st.caption(f"**{heading}**")
            for column in columns:
                value = row[column] if row is not None and column in row.index else np.nan
                stat_row(stat_label(column),
                         f"{value:,.0f}" if pd.notna(value) else "—")

        # One scale for each rating, taken from the pool so the bars are
        # comparable between players. The 95th percentile rather than the max, so
        # a single outlier does not flatten everybody else's bar to nothing.
        for rating, good, label in (("risk_adj", "low", "AdjRisk"),
                                    ("upside_adj", "high", "AdjUpside")):
            value = row[rating] if row is not None else np.nan
            st.caption(label)
            st.markdown(
                rating_bar_html(value, stats[rating].abs().quantile(0.95), good=good),
                unsafe_allow_html=True,
            )

with right:
    with st.container(border=True):
        st.markdown("**Game Log:**")

        position = ctx.player_directory.get_position(canonical_id)

        # 1 row per game played -- season, week, team, opponent_team, and every
        # passing, rushing and receiving stat nflreadpy publishes.
        games = ctx.player_directory.get_gamelog(canonical_id)

        if games.empty:
            st.info("No recorded games — normal for a rookie.")
        else:
            # Newest first, so the default lands on the season you care about.
            seasons = sorted(games["season"].unique(), reverse=True)
            season = st.selectbox(
                "Season", seasons, index=0, width=140,
                key=f"gamelog_season_{canonical_id}",
            )
            # A segmented control returns None when its selection is cleared.
            season = season if season in seasons else seasons[0]

            scored = add_fantasy_points(games[games["season"] == season],
                                        ScoringFormat(fmt_value))
            table, present = shape(scored, columns_for(position))

            st.dataframe(
                table,
                hide_index=True,
                width="stretch",
                column_config={
                    column.field: (
                        st.column_config.NumberColumn(
                            column.label, format=column.format,
                            help=column.help, width=column.width)
                        if column.format
                        else st.column_config.TextColumn(
                            column.label, help=column.help, width=column.width)
                    )
                    for column in present
                },
            )
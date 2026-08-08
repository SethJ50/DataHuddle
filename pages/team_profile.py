"""Page showing one NFL team: its projected depth chart, plus your notes on it.

The left column picks the team and holds a free-text scratchpad about it. The
right column shows a projected depth chart — the best few projected players at
each position — where each player row can be tagged and annotated inline, and
the whole set saved at once.

Notes and markings are scoped to a draft, so a draft must be selected in the
sidebar before anything is editable; without one the tables render read-only.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st
from streamlit_state import get_app_context
from scoring import ScoringFormat

from ui_helpers import draft_selector, FORMAT_LABELS, adp_to_round_pick, load_sim_board
from registry import MARKING_CATEGORIES
from presentation.marks import mark_column_config

ctx = get_app_context()

with st.sidebar:
    st.header("Draft")
    draft = draft_selector(ctx, "team_profile")

# 1 row per team -- team_abbr, team_name, team_logo_espn, team_conf, etc. All 36
# rows, including relocated franchises (LA/STL Rams, OAK, SD) with no projections.
teams = ctx.nfl_read_repo.teams()

# The 32 active teams, so the dropdown never offers a franchise whose roster comes
# back empty. These match FFB's team codes, so team_abbr filters projections directly.
ACTIVE_TEAM_ABBRS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
    "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
}
teams = teams[teams["team_abbr"].isin(ACTIVE_TEAM_ABBRS)]

# Dropdown shows full names; we map the chosen name back to its abbr + logo.
teams_by_name = teams.set_index("team_name")
sorted_team_names = sorted(teams_by_name.index)

# How many of each position make up the "roster" view.
ROSTER_SLOTS = {"QB": 2, "RB": 3, "WR": 5, "TE": 2}

left, right = st.columns([3, 9])

with left:
    logo_slot = st.empty()

    selected_team = st.selectbox(
        "Team", sorted_team_names, index=0, label_visibility="collapsed"
    )
    team_row = teams_by_name.loc[selected_team]     # the chosen team's full record
    team_abbr = team_row["team_abbr"]

    with logo_slot.container(horizontal_alignment="center"):
        st.image(team_row["team_logo_espn"], width=200)

    # Per-draft team notes: a free-text scratchpad tied to (draft_id, team_abbr).
    st.divider()
    st.caption("Team Notes")
    if draft is None:
        st.info("Select a draft in the sidebar to add team notes.")
    else:
        # The key includes draft + team so switching either loads that note rather
        # than reusing the last widget's text -- `value=` only applies to a new key.
        saved_note = ctx.team_notes_service.get(draft["draft_id"], team_abbr)
        note_text = st.text_area(
            "Team Notes",
            value=saved_note,
            key=f"team_note_{draft['draft_id']}_{team_abbr}",
            label_visibility="collapsed",
            height=200,
        )
        if st.button("Save team notes", key="save_team_note"):
            ctx.team_notes_service.save(draft["draft_id"], team_abbr, note_text)
            st.toast("Team notes saved")


with right:
    depth_chart_tab, tab2 = st.tabs(["Depth Chart", "Other"])

    with depth_chart_tab:
        st.subheader("Projected Depth Chart")

        # From the draft, not a dropdown -- a chart ranked in a format your league
        # doesn't use is a trap. Stored as the .value string ("half_ppr"), which is
        # what the column name needs. Half PPR without a draft, as the dropdown did.
        fmt_value = draft["scoring_format"] if draft else ScoringFormat.HALF_PPR.value
        points_col = f"fantasy_points_{fmt_value}_season"

        st.caption(
            f"Projected points in {FORMAT_LABELS.get(fmt_value, fmt_value)}"
            + ("" if draft else " (default — no draft selected)")
        )

        with st.container():
            depth_1_col, depth_2_col = st.columns([10, 2])

            with depth_1_col:
                @st.cache_data(show_spinner="Loading projections...")
                def load_projection_board():
                    """Load every player's blended projection, reusing it between reruns.

                    Streamlit re-runs this whole file whenever any widget changes,
                    and rebuilding the projections each time would make every
                    click slow. The `@st.cache_data` decorator above keeps the
                    result and hands back the same table instantly.

                    Steps:
                        1. Ask the projections service for the blend of all three
                           analysts, which covers every scoring format at once.

                    Returns:
                        pd.DataFrame: One row per player, with `canonical_id`,
                            `name`, `team` (an FFB abbreviation), `position`, and
                            `fantasy_points_<fmt>_season` plus `_per_game` for
                            all three scoring formats.
                    """
                    proj = ctx.projections_service.get_own_projections()
                    return proj

                proj = load_projection_board()

                @st.cache_data(show_spinner="Loading platform ADP...")
                def load_platform_adp(platform, fmt_value):
                    """Look up where one platform drafts every player, as a lookup table.

                    Building the comparison touches all three platforms and the
                    identity mapping, so it is far too slow to redo whenever a
                    checkbox is ticked. The `@st.cache_data` decorator above keeps
                    the result per platform and scoring format.

                    Steps:
                        1. Ask the ADP comparison service for its table, rebuilding
                           the ScoringFormat enum from the stored string.
                        2. Pick out the one column belonging to this platform.
                        3. Return an empty lookup for a platform name that has no
                           column, so an unrecognised value leaves the ADP blank
                           rather than taking the page down.

                    Args:
                        platform: Where the league drafts: "espn", "yahoo", or
                            "sleeper". Also part of the cache key.
                        fmt_value: The scoring format as its stored string, such as
                            "half_ppr". Also part of the cache key.

                    Returns:
                        dict: Maps a canonical player id to his ADP on that
                            platform, as an overall pick number. A player that
                            platform does not rank is simply absent.
                    """
                    # 1 row per in-scope player -- canonical_id, display_name,
                    # headshot_url, position, espn_adp, yahoo_adp, sleeper_adp.
                    comparison = ctx.adp_comparison_service.compare(ScoringFormat(fmt_value))
                    column = f"{platform}_adp"
                    if column not in comparison.columns:
                        return {}
                    return dict(zip(comparison["canonical_id"], comparison[column]))

                def top_by_position(df, position, n, points_col):
                    """Pick the best few projected players at one position.

                    Used once per position to build the depth chart, so each
                    table shows the players most likely to actually start.

                    Steps:
                        1. Keep only the rows at the requested position.
                        2. Sort by the chosen format's projected points, highest
                           first.
                        3. Keep at most `n` rows. `head` returns fewer without
                           complaint if the team has fewer players there.

                    Args:
                        df: Projection rows already filtered to a single team.
                        position: Which position to select, such as "QB".
                        n: How many players to keep, from ROSTER_SLOTS.
                        points_col: The name of the projected-points column for
                            the scoring format the user selected.

                    Returns:
                        pd.DataFrame: At most `n` rows, best projection first.
                            Empty if the team has nobody at that position.
                    """
                    return (
                        df[df["position"] == position]
                        .sort_values(points_col, ascending=False)
                        .head(n)
                    )

                team_proj = proj[proj["team"] == team_abbr]   # this team's players only

                # All draft-scoped: markings key off (draft_id, canonical_id), and only
                # the draft knows which platform's ADP to show or which simulation to
                # read. Without one there is nothing to load, so the tables read-only.
                if draft is not None:
                    marks = ctx.player_markings_service.all_for_draft(draft["draft_id"])
                    cats_by_id  = {m["canonical_id"]: set(m.get("categories", [])) for m in marks}
                    notes_by_id = {m["canonical_id"]: m.get("notes", "")          for m in marks}

                    plat_adp_by_id = load_platform_adp(draft["platform"], fmt_value)

                    # One cache entry shared with Draft Plan and Sim Viewer. A missing
                    # simulation is normal here, not an error -- the column goes blank.
                    sim_board, sim_error = load_sim_board(ctx, draft, year=2026)
                    if sim_error:
                        sim_adp_by_id = {}
                    else:
                        sim_adp_by_id = {
                            canonical_id: mean_pick
                            for canonical_id, mean_pick in zip(sim_board.table["canonical_id"],
                                                               sim_board.simulated_adp)
                            # Team defenses have no canonical id -- a key nothing matches.
                            if isinstance(canonical_id, str)
                        }
                else:
                    st.info("Select a draft in the sidebar to mark players and add notes.")
                    cats_by_id, notes_by_id = {}, {}
                    plat_adp_by_id, sim_adp_by_id = {}, {}

                edited_tables = {}  # position -> edited DataFrame, collected for one Save

                # One separate table per position, in depth-chart order (QB → RB → WR → TE).
                for position, n in ROSTER_SLOTS.items():
                    st.subheader(f"{position}")
                    slot = top_by_position(team_proj, position, n, points_col)

                    # No draft -> read-only: just player + projected points.
                    if draft is None:
                        view = slot[["name", points_col]].rename(
                            columns={"name": "Player", points_col: "Proj Pts"}
                        )
                        st.dataframe(
                            view,
                            hide_index=True,
                            width="stretch",
                            column_config={
                                "Proj Pts": st.column_config.NumberColumn("Pts", width="small", format="%.0f"),
                            },
                        )
                        continue

                    # Editable: player + points + one checkbox per category + notes.
                    # Keep canonical_id (hidden) -- it's the key markings are saved against.
                    table = slot[["canonical_id", "name", points_col]].rename(
                        columns={"name": "Player", points_col: "Proj Pts"}
                    )

                    # Where the market and the model each have him going, as ROUND.PICK
                    # (the float 1.04 means round 1 pick 4). Blank, not zero, for anyone
                    # a source doesn't rank -- "unranked" and "first overall" differ.
                    table["PlatAdp"] = table["canonical_id"].map(plat_adp_by_id).map(
                        lambda a: adp_to_round_pick(a, draft["num_teams"])
                    )
                    table["SimAdp"] = table["canonical_id"].map(sim_adp_by_id).map(
                        lambda a: adp_to_round_pick(a, draft["num_teams"])
                    )

                    # Seed each checkbox from the saved markings. `c=cat` captures the
                    # current category; without it every column binds to the last one.
                    for cat in MARKING_CATEGORIES:
                        table[cat] = table["canonical_id"].map(
                            lambda cid, c=cat: c in cats_by_id.get(cid, set())
                        )
                    # Seed notes from saved markings.
                    table["Notes"] = table["canonical_id"].map(lambda cid: notes_by_id.get(cid, ""))

                    # Player is pinned so it stays visible while scrolling right. Widths
                    # are pixel ints -- "small"/"medium"/"large" also work, nothing else.
                    col_cfg = {
                        "canonical_id": None,   # hidden -- used only for saving
                        "Player":   st.column_config.TextColumn("Player", width="medium", pinned=True),
                        "Proj Pts": st.column_config.NumberColumn("ProjPts", width=60, format="%.0f"),
                        # "%.2f" turns the encoded float 1.04 into the text "1.04".
                        "PlatAdp":  st.column_config.NumberColumn(
                            "PlatAdp", width=80, format="%.2f",
                            help=f"Where {draft['platform'].upper()} has him going in "
                                 f"{FORMAT_LABELS.get(fmt_value, fmt_value)}, as "
                                 f"ROUND.PICK. Blank if that platform doesn't rank him.",
                        ),
                        "SimAdp":   st.column_config.NumberColumn(
                            "SimAdp", width=80, format="%.2f",
                            help="The average pick he went at across the simulated "
                                 "drafts, as ROUND.PICK. Blank for a kept player, for "
                                 "anyone outside the simulated pool, and when no "
                                 "simulation has been run for this draft's settings.",
                        ),
                        "Notes":    st.column_config.TextColumn("Notes", width="medium"),
                    }
                    # Headers, tooltips and widths from presentation/marks.py, so this
                    # editor can't disagree with the draft plan board. No position arg:
                    # every category shows everywhere, which the save loop below assumes.
                    for column, settings in mark_column_config(editable=True).items():
                        col_cfg[column] = st.column_config.CheckboxColumn(**settings)

                    # Keyed by team + position, so switching teams resets the editor
                    # instead of carrying edits onto a different roster.
                    edited_tables[position] = st.data_editor(
                        table,
                        hide_index=True,
                        width="stretch",
                        key=f"editor_{draft['draft_id']}_{team_abbr}_{position}",
                        # Checkboxes + Notes only; anything computed is read-only.
                        disabled=["Player", "Proj Pts", "PlatAdp", "SimAdp"],
                        column_config=col_cfg,
                    )

                # Single Save button persists every row of all four tables at once.
                if draft is not None and st.button("Save markings", type="primary"):
                    for edited in edited_tables.values():
                        for _, row in edited.iterrows():
                            chosen = [cat for cat in MARKING_CATEGORIES if bool(row[cat])]
                            ctx.player_markings_service.save(
                                draft["draft_id"], row["canonical_id"], chosen, row["Notes"]
                            )
                    st.toast("Markings saved")


    with tab2:
        st.subheader("Other Tab")

    

    


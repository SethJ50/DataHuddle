import streamlit as st
from streamlit_state import get_app_context
from scoring import ScoringFormat

from ui_helpers import draft_selector
from registry import MARKING_CATEGORIES

ctx = get_app_context()

with st.sidebar:
    st.header("Draft")
    draft = draft_selector(ctx, "team_profile")

# teams(): 1 row per team — team_abbr, team_name, team_logo_espn, team_conf, etc.
# Note: this table has 36 rows because it includes relocated/defunct franchises
# (LA/STL Rams, OAK Raiders, SD Chargers) that have no current projections.
teams = ctx.nfl_read_repo.teams()

# Keep only the 32 active teams so the dropdown never offers a franchise whose
# roster would come back empty. These abbreviations match FFB's team codes
# exactly (verified), so team_abbr can be used directly as the projections filter.
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
        # Seed the text area from the saved note. The key includes draft + team
        # so switching either loads the correct note instead of reusing the
        # previous widget's text (value= only applies when the key is new).
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


# Pretty labels for the 3 formats the projections service produces.
FORMAT_LABELS = {
    ScoringFormat.REGULAR.value:  "Standard",
    ScoringFormat.HALF_PPR.value: "Half PPR",
    ScoringFormat.FULL_PPR.value: "Full PPR",
}

# Short header + full-name tooltip for each marking category. Checkbox column
# width is driven mostly by header text, so the abbreviations keep all six
# categories fitting horizontally; the full name shows on hover via `help`.
CATEGORY_HEADERS = {
    "Safe":                ("Safe", "Safe"),
    "Upside":              ("Ups",  "Upside"),
    "Love":                ("Love",    "Love"),
    "Like":                ("Like", "Like"),
    "Uncertain Backfield": ("BF?",  "Uncertain Backfield"),
    "New Top 12 Receiver": ("T12",  "New Top 12 Receiver"),
}

with right:
    depth_chart_tab, tab2 = st.tabs(["Depth Chart", "Other"])

    with depth_chart_tab:
        st.subheader("Projected Depth Chart")
        
        with st.container():
            col1, col2 = st.columns([3, 9])
            with col1:
                label_to_format = {label: fmt for fmt, label in FORMAT_LABELS.items()}
                chosen_label = st.selectbox("Scoring format", list(label_to_format.keys()), index=1)  # default Half PPR
                fmt_value = label_to_format[chosen_label]          # e.g. "half_ppr"

                points_col = f"fantasy_points_{fmt_value}_season"

        with st.container():
            depth_1_col, depth_2_col = st.columns([10, 2])

            with depth_1_col:
                @st.cache_data(show_spinner="Loading projections...")
                def load_projection_board():
                    # get_own_projections(): per-player FFB projection rows — includes
                    # name, team (FFB abbr), position (QB/RB/WR/TE), canonical_id, and
                    # fantasy_points_<fmt>_season / _per_game for ALL three formats.
                    proj = ctx.projections_service.get_own_projections()
                    return proj

                proj = load_projection_board()

                def top_by_position(df, position, n, points_col):
                    # Purpose: the n highest-projected players at one position for a team.
                    # Parameters: df (already filtered to one team), position ("QB"/"RB"/...),
                    #             n (slot count from ROSTER_SLOTS), points_col (chosen format's season pts).
                    # Returns: DataFrame slice, sorted best-first, at most n rows.
                    return (
                        df[df["position"] == position]
                        .sort_values(points_col, ascending=False)
                        .head(n)
                    )

                # Filter the full projection board down to just the selected team.
                team_proj = proj[proj["team"] == team_abbr]        # this team's projected players only

                # Markings live per (draft_id, canonical_id). Without a selected draft
                # we can't load or save them, so the tables render read-only.
                if draft is not None:
                    marks = ctx.player_markings_service.all_for_draft(draft["draft_id"])
                    cats_by_id  = {m["canonical_id"]: set(m.get("categories", [])) for m in marks}
                    notes_by_id = {m["canonical_id"]: m.get("notes", "")          for m in marks}
                else:
                    st.info("Select or create a draft in the sidebar to mark players and add notes.")
                    cats_by_id, notes_by_id = {}, {}

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
                    # Seed each category checkbox from saved markings. The c=cat default
                    # arg captures the current category so every column doesn't bind to
                    # the last loop value.
                    for cat in MARKING_CATEGORIES:
                        table[cat] = table["canonical_id"].map(
                            lambda cid, c=cat: c in cats_by_id.get(cid, set())
                        )
                    # Seed notes from saved markings.
                    table["Notes"] = table["canonical_id"].map(lambda cid: notes_by_id.get(cid, ""))

                    # Condensed column config: short checkbox headers (full name in the
                    # tooltip) + small widths so all 6 categories fit horizontally; pin
                    # Player so it stays visible while scrolling right.
                    col_cfg = {
                        "canonical_id": None,   # hidden -- used only for saving
                        "Player":   st.column_config.TextColumn("Player", width="medium", pinned=True),
                        "Proj Pts": st.column_config.NumberColumn("ProjPts", width="80px", format="%.0f"),
                        "Notes":    st.column_config.TextColumn("Notes", width="medium"),
                    }
                    for cat in MARKING_CATEGORIES:
                        short, full = CATEGORY_HEADERS[cat]
                        col_cfg[cat] = st.column_config.CheckboxColumn(short, help=full, width="40px")

                    # key includes team + position so switching teams resets the editor
                    # state instead of carrying edits onto a different roster.
                    edited_tables[position] = st.data_editor(
                        table,
                        hide_index=True,
                        width="stretch",
                        key=f"editor_{draft['draft_id']}_{team_abbr}_{position}",
                        disabled=["Player", "Proj Pts"],   # only checkboxes + notes editable
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

    

    


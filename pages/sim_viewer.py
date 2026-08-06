"""Inspect the Monte Carlo simulation behind a draft.

Two ways of looking at the same picks matrix:

  Player View -- collapses all 10,000 simulated drafts into one row per player.
      Answers "does the model believe sensible things about this guy, and did
      calibration actually hit the ADP it was aiming at?"

  Sim View    -- expands ONE simulated draft back into a draft board. Answers
      "what does a draft this model produces actually look like?", which is the
      check that summary statistics cannot give you. Positional runs, kickers
      going too early, a tier emptying in four picks -- those are visible here
      and invisible in an average.

Read-only throughout. Nothing on this page changes a draft, a plan, or an
artifact; it only reads what scripts/run_draft_sim.py already wrote.
"""

import numpy as np
import pandas as pd
import streamlit as st

from streamlit_state import get_app_context
from registry import Position
from ui_helpers import draft_selector, load_sim_board, PLATFORM_LABELS
from draft_model.calibrate import simulated_mean_pick, simulated_stdev_pick
from draft_model.mechanics import snake_order
from draft_model.queries import availability_matrix, pick_percentiles, sim_draft_order
from presentation.draft_board_view import BoardEntry, build_board_grid

ctx = get_app_context()

YEAR = 2026

# Background for your own team's column on the draft board. A translucent color
# so it tints the cell in both light and dark themes rather than fighting them.
MY_TEAM_TINT = "rgba(59, 130, 246, 0.18)"

st.title("Simulation Viewer")

with st.sidebar:
    st.header("Draft")
    draft = draft_selector(ctx, "sim_viewer")
    if draft is None:
        st.stop()

# Shared with the Draft Plan page, so the picks matrix is loaded once for both.
board, board_error = load_sim_board(ctx, draft, year=YEAR)

if board_error:
    st.warning(
        f"**No simulation for this draft's current settings.**\n\n"
        f"```\n{board_error}\n```",
        icon=":material/warning:",
    )
    st.stop()

if board.stale:
    st.warning(
        "The player pool has changed since this simulation ran, so the numbers "
        "below describe a slightly different set of players. Re-run "
        "`scripts/run_draft_sim.py`.",
        icon=":material/warning:",
    )

config = board.config
picks = board.artifact.picks              # (n_sims, n_players) of pick numbers
table = board.table                       # one row per player, SAME order as picks columns

st.caption(
    f"{board.n_sims:,} simulated drafts · {len(table)} players · "
    f"{config.num_teams} teams, pick {config.draft_position}, {config.num_rounds} rounds · "
    f"drafting on {PLATFORM_LABELS.get(config.platform, config.platform)} · "
    f"{'calibrated' if board.calibrated else 'NOT calibrated (raw ADP)'}"
)

player_tab, sim_tab = st.tabs(["Player view", "Sim view"])


# ---------------------------------------------------------------------------
# Tab 1: one row per player, summarising every simulation
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner="Reading platform ADP...")
def platform_adp_columns(fmt, platform: str) -> pd.DataFrame:
    """Load the two ADP numbers shown beside the model's own target.

    Having the blend and the single platform's raw number side by side is what
    lets you see whether the model's target drifted for a good reason.

    Steps:
        1. Ask the sim service for the weighted blend across all three
           platforms.
        2. Load the full comparison table and label its rows by canonical id.
        3. Pull out this platform's own ADP column, falling back to a column of
           blanks if that platform is not one of the three.
        4. Put both into a two-column table.

    Args:
        fmt: Which scoring format's ADP to read. Also part of the cache key.
        platform: The platform this league drafts on. Also part of the cache key.

    Returns:
        pd.DataFrame: Labelled by canonical id, with `blend_adp` (the weighted
            ESPN/Yahoo/Sleeper consensus) and `platform_adp` (that one
            platform's raw number).

    Note:
        Cached because both come from service calls that re-read and re-join
        every platform's ADP export, and Streamlit reruns this whole script on
        any click. Keyed on format and platform, which is everything they depend
        on.

        Indexed by canonical_id (the app's own player id) rather than
        ffc_player_id, because that is the only key these two sources share with
        the model table. Team defenses have no canonical_id and so get NaN here,
        which is correct -- no platform ADP export lists them consistently.
    """
    blend = ctx.draft_sim_service.platform_blend(fmt, platform)
    comparison = ctx.adp_comparison_service.compare(fmt).set_index("canonical_id")

    column = f"{platform}_adp"
    raw = (comparison[column] if column in comparison.columns
           else pd.Series(np.nan, index=comparison.index))

    return pd.DataFrame({"blend_adp": blend, "platform_adp": raw})


@st.cache_data(show_spinner="Summarising simulations...")
def player_summary(signature: str) -> pd.DataFrame:
    """Collapse the whole picks matrix into one row per player.

    Turns 10,000 simulated drafts into a table you can read: where each player
    typically went, how much that varied, and how close the simulation landed to
    the ADP it was aiming at.

    Steps:
        1. Summarize the matrix with `simulated_mean_pick` and
           `simulated_stdev_pick` from draft_model/calibrate.py, and get the
           best and worst case picks from `pick_percentiles` in
           draft_model/queries.py.
        2. Attach the model's own target ADP alongside, so the two can be
           compared directly.
        3. Attach the blend and platform ADP by joining on canonical id.
        4. Add availability at your own picks via `availability_matrix`.

    Args:
        signature: The board's cache signature. NEVER READ inside this function
            — it is here purely so the cache invalidates when the underlying
            simulation does. `board` is a module-level object this function
            closes over, and closing over it is what makes the argument
            necessary: without a changing key, Streamlit would keep serving the
            previous draft's summary.

    Returns:
        pd.DataFrame, one row per player in the simulation's column order, with:
            Player, Pos, Team
            Sim ADP     -- mean simulated pick, undrafted outcomes excluded
            Target ADP  -- what calibration was AIMING at (adp_target)
            Blend ADP   -- weighted ESPN/Yahoo/Sleeper consensus
            <Plat> ADP  -- the drafting platform's own raw ADP
            Sim SD      -- spread of simulated picks
            High, Low   -- 5th and 95th percentile simulated pick
            VORP, Proj  -- value over replacement, projected season points
            R1..Rn      -- probability he is still available at your pick that round

    Note:
        Sim ADP vs Target ADP is the single most useful comparison on this page.
        They should agree closely for players drafted in most simulations; a
        large gap means calibration could not reach the target, which is worth
        knowing before you trust any availability number for that player.

        High/Low are pick numbers, so SMALLER IS BETTER -- "High" is his earliest
        realistic pick, matching how FFC labels the same idea.
    """
    frame = pd.DataFrame({
        "Player": table["name"].to_numpy(),
        "Pos": table["position"].to_numpy(),
        "Team": table["team"].to_numpy() if "team" in table.columns else "",
    })

    # --- where that target came from ---
    extra = platform_adp_columns(config.scoring_format, config.platform)
    canonical = table["canonical_id"]
    frame[f"{PLATFORM_LABELS.get(config.platform, config.platform)} ADP"] = \
        canonical.map(extra["platform_adp"]).to_numpy()
    frame["Blend ADP"] = canonical.map(extra["blend_adp"]).to_numpy()

    # --- what the simulation actually did ---
    frame["Sim ADP"] = simulated_mean_pick(picks)
    frame["Target ADP"] = table["adp_target"].to_numpy()
    
    # --- spread ---
    frame["Sim SD"] = simulated_stdev_pick(picks)
    bounds = pick_percentiles(picks, (5.0, 95.0))
    frame["High"] = bounds[:, 0]
    frame["Low"] = bounds[:, 1]

    # --- value ---
    frame["VORP"] = board.vorp
    frame["Proj"] = table["projection"].to_numpy() if "projection" in table.columns else np.nan

    # --- availability at each of YOUR picks, one column per round ---
    grid = availability_matrix(picks, config.my_picks)
    for round_number, pick in enumerate(config.my_picks, start=1):
        frame[f"R{round_number} (#{pick})"] = grid[:, round_number - 1]

    return frame


with player_tab:
    summary = player_summary(ctx.draft_sim_service.board_signature(draft, year=YEAR))

    # Only offer positions that are actually in the pool, in the canonical order
    # from the registry rather than alphabetically.
    present = set(summary["Pos"])
    choices = ["All"] + [p.value for p in Position if p.value in present]
    position = st.selectbox("Position", choices, index=0)

    shown = summary if position == "All" else summary[summary["Pos"] == position]

    # Percent columns are the availability grid; everything else is a plain number.
    round_columns = [c for c in shown.columns if c.startswith("R") and "(#" in c]
    column_config = {
        "Player": st.column_config.TextColumn(pinned=True),
        "Sim ADP": st.column_config.NumberColumn(format="%.1f", help="mean simulated pick, undrafted outcomes excluded"),
        "Target ADP": st.column_config.NumberColumn(format="%.1f", help="what calibration was AIMING at (adp_target)"),
        "Blend ADP": st.column_config.NumberColumn(format="%.1f"),
        "Sim SD": st.column_config.NumberColumn(format="%.1f"),
        "High": st.column_config.NumberColumn(format="%.0f"),
        "Low": st.column_config.NumberColumn(format="%.0f"),
        "VORP": st.column_config.NumberColumn(format="%.0f"),
        "Proj": st.column_config.NumberColumn(format="%.0f"),
    }
    column_config[f"{PLATFORM_LABELS.get(config.platform, config.platform)} ADP"] = \
        st.column_config.NumberColumn(format="%.1f")
    for column in round_columns:
        column_config[column] = st.column_config.NumberColumn(
            format="percent",
            help=f"Chance this player is still on the board at pick "
                 f"{column.split('#')[1].rstrip(')')}",
        )

    st.dataframe(shown, column_config=column_config, hide_index=True, height=600)

    st.caption(
        "**Sim ADP vs Target ADP** is the calibration check — they should be close. "
        "**High** and **Low** are the 5th and 95th percentile simulated picks, so "
        "smaller is earlier. **R1…Rn** is the chance the player is still available "
        "at your pick in that round."
    )


# ---------------------------------------------------------------------------
# Tab 2: one simulated draft, laid out as a board
# ---------------------------------------------------------------------------


def build_sim_board(sim_index: int) -> pd.DataFrame:
    """Turn one simulated draft into a classic draft-board grid.

    Summary statistics cannot show you a positional run or a tier emptying in
    four picks. Laying one simulated draft out as a board makes those visible.

    Steps:
        1. Pull the player names and positions out of the model table.
        2. Build an empty grid with one row per round and one column per team.
        3. Replay the draft in pick order using `sim_draft_order` from
           draft_model/queries.py, which hands back pick numbers paired with the
           column of the player taken.
        4. Convert each pick number to a round, skipping anything past the end of
           the board as a defensive measure.
        5. Ask `snake_order` from draft_model/mechanics.py which team made the
           pick, and write the entry into that team's column.
        6. Build the column headings, marking your own slot.

    Args:
        sim_index: Which simulation to replay, counting from 0.

    Returns:
        pd.DataFrame: One row per round and one column per team slot. Each cell
            reads like "12. Bijan Robinson (RB)" — the overall pick number, then
            who went there. Empty where no pick landed.

    Note:
        THE SNAKE IS IN THE DATA, NOT THE LAYOUT. Columns are fixed team slots
        (slot 1 always leftmost), and each pick is placed in the column of the
        team that made it. Because the draft order reverses every round, even
        rounds therefore read right-to-left — which is exactly how a real draft
        board looks, and it makes the snake visible instead of hiding it behind
        a re-sorted row.

        snake_order is the one and only source for "whose pick is this". Working
        it out again here with fresh arithmetic is how the board and the
        simulation would drift into disagreeing, and the failure would look
        completely plausible.
    """
    names = table["name"].to_numpy()
    positions = table["position"].to_numpy()
    kept_picks = set(config.keeper_picks)

    def entries():
        for pick_number, column in zip(*sim_draft_order(picks, sim_index)):
            team = snake_order(pick_number, config.num_teams,
                               config.third_round_reversal) + 1
            marker = " (K)" if pick_number in kept_picks else ""
            yield BoardEntry(
                pick_number, team,
                f"{pick_number}. {names[column]} ({positions[column]}){marker}",
                positions[column],
            )

    return build_board_grid(entries(), config, my_slot=config.draft_position)


def randomize_sim():
    """Jump the sim viewer to a randomly chosen simulation.

    Registered as a button's click handler. Browsing a few random drafts is the
    quickest way to sanity-check that the model produces plausible boards.

    Steps:
        1. Pick a random simulation number within the range this run holds.
        2. Write it into the number input's own session state key. Assigning to a
           widget's key before the page re-runs is how one widget changes
           another's value in Streamlit.

    Returns:
        None: The new value is stored in session state, and Streamlit re-runs the
            page to reflect it.
    """
    st.session_state["sim_viewer_index"] = int(
        np.random.default_rng().integers(0, board.n_sims)
    )


with sim_tab:
    # Guard against a stored index left over from a run with more simulations --
    # number_input raises if its current value sits outside min/max.
    if st.session_state.get("sim_viewer_index", 0) > board.n_sims - 1:
        st.session_state["sim_viewer_index"] = 0

    with st.container(horizontal=True, vertical_alignment="bottom"):
        st.number_input(
            "Simulation", min_value=0, max_value=board.n_sims - 1, step=1,
            key="sim_viewer_index",
            help=f"Which of the {board.n_sims:,} simulated drafts to replay.",
        )
        st.button("Random", icon=":material/casino:", on_click=randomize_sim)

    sim_index = st.session_state["sim_viewer_index"]
    sim_board = build_sim_board(sim_index)

    # Tint your own column. Coloring is the one job Pandas Styler keeps here --
    # all value formatting is column_config's.
    my_column = sim_board.columns[config.draft_position - 1]
    styled = sim_board.style.set_properties(
        subset=[my_column], **{"background-color": MY_TEAM_TINT}
    )

    st.dataframe(
        styled,
        column_config={c: st.column_config.TextColumn(width="medium")
                       for c in sim_board.columns},
        height=min(80 + 35 * config.num_rounds, 700),
    )

    st.caption(
        f"Simulation {sim_index:,} of {board.n_sims:,}. Columns are team slots, so "
        f"even rounds read right-to-left — that's the snake. Your slot "
        f"({config.draft_position}) is highlighted."
    )

    if config.keepers:
        st.caption(
            f":material/info: This draft has {len(config.keepers)} keeper(s), marked "
            f"**(K)**. A keeper is off the board from pick 1 and his team spends "
            f"that round's pick on him, so this draft makes "
            f"{config.total_picks - len(config.keepers)} real selections rather "
            f"than {config.total_picks}."
        )
        st.caption(
            ":material/info: Expect **Sim ADP** to read earlier than **Target ADP** "
            "here. Target ADP comes from redraft drafts where nobody was kept; "
            "taking the keepers out of the pool genuinely pulls everyone else "
            "forward. The gap grows with how good the kept players are."
        )

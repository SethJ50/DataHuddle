"""Tests for roster slotting and the draft board grid.

Both are pure functions, so no Streamlit is involved. The two things worth
pinning down are that the FLEX slot gets a LEFTOVER rather than your best
running back, and that the board's even rounds read right-to-left.
"""

import pandas as pd
import pytest

from presentation.colors import POSITION_TINTS
from presentation.draft_board_view import (
    BoardEntry, build_board_grid, build_position_grid, entries_from_pick_log,
    equal_column_widths, tint_by_position,
)
from presentation.roster_view import roster_frame, slot_roster

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}


def team_of(*rows):
    return pd.DataFrame(rows, columns=["name", "position", "projection"])


def test_the_lineup_reads_in_the_conventional_order():
    lineup = slot_roster(team_of(("A", "QB", 300)), SLOTS)
    assert [label for label, _ in lineup] == [
        "QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "K", "DST"]


def test_the_best_player_at_a_position_starts():
    lineup = slot_roster(team_of(("Good", "RB", 280), ("Better", "RB", 300)), SLOTS)
    assert dict(lineup)["RB1"]["name"] == "Better"


def test_flex_gets_the_leftover_not_your_best_back():
    # Filling FLEX first would put your RB1 in the flex and leave RB1 empty.
    lineup = slot_roster(team_of(
        ("RB_A", "RB", 280), ("RB_B", "RB", 240), ("RB_C", "RB", 150)), SLOTS)
    assert dict(lineup)["RB1"]["name"] == "RB_A"
    assert dict(lineup)["RB2"]["name"] == "RB_B"
    assert dict(lineup)["FLEX"]["name"] == "RB_C"


def test_unfilled_slots_are_shown_rather_than_hidden():
    # An empty RB2 in round 9 is the thing you want the panel to shout about.
    lineup = slot_roster(team_of(("A", "QB", 300)), SLOTS)
    assert dict(lineup)["RB1"] is None
    assert roster_frame(lineup).loc[1, "Player"] == "—"


def test_an_empty_roster_still_renders_every_slot():
    lineup = slot_roster(team_of(), SLOTS)
    assert len(lineup) == 9
    assert all(player is None for _, player in lineup)


def test_extra_players_go_to_the_bench_in_order():
    lineup = slot_roster(team_of(
        ("QB1", "QB", 300), ("QB2", "QB", 200), ("QB3", "QB", 100)), SLOTS)
    bench = [(label, p["name"]) for label, p in lineup if label.startswith("BN")]
    assert bench == [("BN1", "QB2"), ("BN2", "QB3")]


def test_a_player_with_no_projection_does_not_outrank_a_real_one():
    lineup = slot_roster(team_of(("Unknown", "RB", None), ("Known", "RB", 100)), SLOTS)
    assert dict(lineup)["RB1"]["name"] == "Known"


# --------------------------------------------------------------------------
# The board grid
# --------------------------------------------------------------------------

class Config:
    num_teams, num_rounds = 4, 3


def log(*entries):
    return [{"pick": p, "team": t, "canonical_id": c, "source": s}
            for p, t, c, s in entries]


def test_even_rounds_read_right_to_left():
    # The snake lives in the data, not the layout. Pick 5 belongs to team 4.
    grid = build_board_grid(entries_from_pick_log(
        log((5, 4, "a", "auto"), (6, 3, "b", "auto")), {"a": "A", "b": "B"}), Config)
    assert grid.loc["R2"].iloc[3].startswith("5.")
    assert grid.loc["R2"].iloc[2].startswith("6.")


def test_keepers_are_marked():
    grid = build_board_grid(entries_from_pick_log(
        log((1, 1, "a", "keeper")), {"a": "A"}), Config)
    assert "(K)" in grid.loc["R1"].iloc[0]


def test_your_column_is_labelled():
    grid = build_board_grid([], Config, my_slot=2)
    assert "(you)" in grid.columns[1]
    assert "(you)" not in grid.columns[0]


def test_unpicked_cells_are_empty():
    grid = build_board_grid([], Config)
    assert (grid == "").all().all()


def test_an_off_pool_pick_shows_a_placeholder():
    grid = build_board_grid(entries_from_pick_log(
        log((1, 1, None, "user")), {}), Config)
    assert grid.loc["R1"].iloc[0] == "1. —"


def test_a_pick_beyond_the_board_is_ignored_not_fatal():
    grid = build_board_grid(entries_from_pick_log(
        log((99, 1, "a", "auto")), {"a": "A"}), Config)
    assert (grid == "").all().all()


# --------------------------------------------------------------------------
# Position colouring on the board
# --------------------------------------------------------------------------

def test_the_position_grid_matches_the_label_grid_cell_for_cell():
    # The styling function indexes one against the other positionally, so a
    # shape or ordering mismatch would paint the wrong cells.
    entries = [BoardEntry(1, 1, "1. A (RB)", "RB"),
               BoardEntry(6, 3, "6. B (WR)", "WR")]
    labels = build_board_grid(entries, Config)
    positions = build_position_grid(entries, Config)

    assert labels.shape == positions.shape
    assert list(labels.columns) == list(positions.columns)
    assert list(labels.index) == list(positions.index)
    assert positions.loc["R1"].iloc[0] == "RB"
    assert positions.loc["R2"].iloc[2] == "WR"


def test_unpicked_cells_hold_an_empty_string_not_none():
    # .style chokes on None; an empty string simply goes unstyled.
    positions = build_position_grid([], Config)
    assert (positions == "").all().all()
    assert positions.notna().all().all()


def test_the_position_comes_through_as_data_not_parsed_from_the_label():
    # A name containing a bracket would break any attempt to read the position
    # back out of "12. Some (Weird) Name (RB)".
    entries = [BoardEntry(1, 1, "1. Some (Weird) Name (RB)", "RB")]
    assert build_position_grid(entries, Config).loc["R1"].iloc[0] == "RB"


def test_every_position_has_a_tint_and_k_dst_share_one():
    from draft_model.config import POSITIONS
    assert set(POSITION_TINTS) == set(POSITIONS)
    assert POSITION_TINTS["K"] == POSITION_TINTS["DST"]        # folded
    skill = {POSITION_TINTS[p] for p in ("QB", "RB", "WR", "TE")}
    assert len(skill) == 4                                      # all distinct
    assert POSITION_TINTS["K"] not in skill                     # neutral is its own


def test_tinting_paints_filled_cells_and_leaves_blanks_alone():
    entries = [BoardEntry(1, 1, "1. A (RB)", "RB")]
    labels = build_board_grid(entries, Config)
    positions = build_position_grid(entries, Config)

    styles = tint_by_position(positions)(labels)

    assert POSITION_TINTS["RB"] in styles.iloc[0, 0]
    assert styles.iloc[0, 1] == ""            # nobody picked there
    assert styles.shape == labels.shape


def test_an_unknown_position_is_left_unstyled_rather_than_guessed():
    entries = [BoardEntry(1, 1, "1. —", "")]
    labels = build_board_grid(entries, Config)
    styles = tint_by_position(build_position_grid(entries, Config))(labels)
    assert styles.iloc[0, 0] == ""


def test_every_team_column_gets_the_same_width():
    labels = build_board_grid([], Config)
    config = equal_column_widths(labels, width=150)
    assert set(config) == set(labels.columns)


def test_entries_carry_the_position_through_from_the_pick_log():
    picks = [{"pick": 1, "team": 1, "player_id": "7", "canonical_id": "id7",
              "source": "user"}]
    entries = list(entries_from_pick_log(picks, {"7": "Gibbs (RB)"}, {"7": "RB"}))
    assert entries[0].position == "RB"
    assert entries[0].label == "1. Gibbs (RB)"


def test_an_unlisted_picks_own_position_still_colours_its_cell():
    # He has no table row, so the position can only come off the pick itself.
    picks = [{"pick": 1, "team": 1, "player_id": None, "canonical_id": None,
              "source": "unknown", "position": "K"}]
    entries = list(entries_from_pick_log(picks, {}, {}))
    assert entries[0].position == "K"
    assert entries[0].label == "1. Unknown (K)"

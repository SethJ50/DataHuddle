"""Tests for the seam where keepers meet the app: columns, VORP and availability.

draft_model/ knows keepers only as "pick 29 is spent on column 7". The service
layer is what turns "team 3 keeps Bijan in round 2" into that, and what stops a
kept player being presented as something you could draft.

The availability test here is the important one. It guards a bug that shipped:
a kept player's matrix entry is the pick his team spent on him, and
`prob_available_at_pick` asks `pick >= target`, so a keeper held at pick 29 read
as 100% AVAILABLE at every pick before 29. He is not available at any pick --
he was never in the pool.
"""

import numpy as np
import pandas as pd
import pytest

from draft_model.config import UNDRAFTED, DraftConfig, Keeper
from services.draft_sim_service import DraftBoard, DraftSimService
from scoring import ScoringFormat


def make_config(**overrides):
    values = dict(year=2026, num_teams=4, num_rounds=4, draft_position=1,
                  scoring_format=ScoringFormat.FULL_PPR)
    values.update(overrides)
    return DraftConfig(**values)


def make_table(n=8, with_canonical=True):
    """A model table shaped like build_table's output, row order = column order."""
    return pd.DataFrame({
        "ffc_player_id": range(100, 100 + n),
        "canonical_id": [f"id{i}" for i in range(n)] if with_canonical else [None] * n,
        "name": [f"Player {i}" for i in range(n)],
        "position": ["RB", "WR"] * (n // 2),
        "team": ["DET"] * n,
        "adp_target": np.arange(1.0, n + 1.0),
        "projection": np.linspace(200.0, 100.0, n),
    })


class FakeArtifact:
    """Just enough of a loaded artifact for DraftBoard to work on."""

    def __init__(self, picks):
        self.picks = picks
        self.metadata = {"calibrated": True}

    @property
    def n_sims(self):
        return int(self.picks.shape[0])


# --------------------------------------------------------------------------
# Translating keepers into matrix columns
# --------------------------------------------------------------------------

def test_keeper_columns_maps_pick_to_the_right_row():
    # Team 2 of 4, round 1: round 1 runs forwards, so that is pick 2.
    config = make_config(keepers=(Keeper(team=2, round=1, canonical_id="id3"),))
    columns = DraftSimService.keeper_columns(config, make_table())
    assert columns == {2: 3}


def test_keeper_columns_is_empty_for_a_redraft_league():
    assert DraftSimService.keeper_columns(make_config(), make_table()) == {}


def test_keeper_columns_refuses_a_player_missing_from_the_pool():
    # Dropping him silently would be much worse than failing: his pick would go
    # to the best available player, handing that team a selection the real
    # league never gives them.
    config = make_config(keepers=(Keeper(team=1, round=1, canonical_id="nobody"),))
    with pytest.raises(ValueError, match="not in the simulated pool"):
        DraftSimService.keeper_columns(config, make_table())


def test_keeper_columns_ignores_rows_with_no_canonical_id():
    # Team defenses carry no canonical_id. They must not blow up the lookup.
    table = make_table()
    table.loc[5, "canonical_id"] = None
    config = make_config(keepers=(Keeper(team=1, round=1, canonical_id="id0"),))
    assert DraftSimService.keeper_columns(config, table) == {1: 0}


# --------------------------------------------------------------------------
# What the board shows for a kept player
# --------------------------------------------------------------------------

def build_board(keepers=()):
    """A board whose keeper (if any) is recorded at his real keeper pick."""
    config = make_config(keepers=keepers)
    table = make_table()

    # Everyone goes at their ADP; keepers go at the pick their team spends.
    picks = np.tile(np.arange(1, len(table) + 1, dtype=np.int16), (5, 1))
    for pick, canonical_id in config.keeper_picks.items():
        column = int(table.index[table["canonical_id"] == canonical_id][0])
        picks[:, column] = pick

    kept = table["canonical_id"].isin(config.kept_player_ids).to_numpy()
    vorp = np.linspace(50.0, 1.0, len(table))
    vorp[kept] = np.nan

    return DraftBoard(config=config, table=table, artifact=FakeArtifact(picks),
                      vorp=vorp, replacement={"RB": 100.0, "WR": 100.0},
                      stale=False, kept=kept)


def test_a_kept_player_is_never_available():
    # He is held at pick 12, so the raw matrix would call him available at every
    # earlier pick. He is available at none of them.
    board = build_board(keepers=(Keeper(team=1, round=3, canonical_id="id0"),))
    frame = board.availability(target_picks=[1, 5, 12, 16]).set_index("canonical_id")

    row = frame.loc["id0"]
    assert bool(row["kept"])
    for pick in (1, 5, 12, 16):
        assert row[f"P@{pick}"] == 0.0


def test_an_unkept_player_keeps_his_real_availability():
    # The zeroing must be surgical: only kept rows, nobody else.
    board = build_board(keepers=(Keeper(team=1, round=3, canonical_id="id0"),))
    frame = board.availability(target_picks=[1, 5]).set_index("canonical_id")

    assert not bool(frame.loc["id6"]["kept"])
    assert frame.loc["id6"]["P@5"] == 1.0     # goes at pick 7, so still there at 5


def test_a_kept_player_has_no_cost_of_waiting():
    # There is no decision to make about a player you cannot have. He reads BLANK
    # rather than 0.0: a zero is a measurement -- "waiting costs you nothing" --
    # and this is the absence of one. It also keeps him out of any sort or
    # comparison on the column, where a 0 would sit among real values.
    board = build_board(keepers=(Keeper(team=1, round=3, canonical_id="id0"),))
    frame = board.availability().set_index("canonical_id")
    assert pd.isna(frame.loc["id0"]["cost_of_waiting"])


def test_a_kept_player_is_not_the_fallback_anyone_waits_for():
    # The baseline is "best available at this position next round". A kept player
    # is available to nobody, so counting him would understate every cost at his
    # position -- and understate it most for exactly the position he plays.
    board = build_board(keepers=(Keeper(team=1, round=3, canonical_id="id0"),))
    frame = board.availability().set_index("canonical_id")

    same_position = board.table.loc[board.table["canonical_id"] == "id0",
                                    "position"].iloc[0]
    others = frame[(frame["position"] == same_position) & ~frame["kept"]]
    assert others["cost_of_waiting"].notna().all()


def test_kept_players_are_excluded_from_tier_survival():
    # Asking "will one of these three last?" must not be answered yes on the
    # strength of a player somebody is already keeping.
    board = build_board(keepers=(Keeper(team=1, round=3, canonical_id="id0"),))
    assert board.tier_survival(["Player 0"], target_pick=2) == 0.0


def test_availability_defaults_to_the_picks_i_can_actually_use():
    # My own keeper spends one of my picks, so it must not appear as a column to
    # plan a selection at.
    keeper = Keeper(team=1, round=2, canonical_id="id0")
    board = build_board(keepers=(keeper,))
    spent = next(iter(board.config.keeper_picks))

    frame = board.availability()
    assert f"P@{spent}" not in frame.columns
    assert len(board.config.my_selectable_picks) == board.config.num_rounds - 1


def test_a_redraft_board_marks_nobody_as_kept():
    board = build_board()
    frame = board.availability()
    assert not frame["kept"].any()
    assert board.kept_mask().sum() == 0

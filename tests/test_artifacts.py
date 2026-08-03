"""Tests for simulation persistence.

The theme: a matrix that cannot be interpreted is worse than no matrix, because
it still produces numbers. These tests make the unreadable cases fail loudly.
"""

import numpy as np
import pytest

from draft_model.artifacts import (
    artifact_path, load_picks_matrix, matches_table, save_picks_matrix,
)
from draft_model.config import UNDRAFTED, DraftConfig
from scoring import ScoringFormat


def make_config(**overrides):
    values = dict(year=2026, num_teams=12, num_rounds=15, draft_position=5,
                  scoring_format=ScoringFormat.FULL_PPR)
    values.update(overrides)
    return DraftConfig(**values)


def make_picks():
    # Two simulations, four players; one player undrafted in each.
    return np.array([[1, 2, 3, UNDRAFTED],
                     [2, 1, UNDRAFTED, 3]], dtype=np.int16)


def test_round_trip_preserves_everything(tmp_path):
    config = make_config()
    picks = make_picks()
    ids = ["101", "102", "103", "104"]

    path = save_picks_matrix(tmp_path / "run.npz", picks, config, ids,
                             mu=np.array([1.0, 2.0, 3.0, 4.0]),
                             sd=np.array([0.5, 0.6, 0.7, 0.8]),
                             metadata={"note": "smoke"})
    loaded = load_picks_matrix(path)

    assert np.array_equal(loaded.picks, picks)
    assert list(loaded.player_ids) == ids
    assert loaded.n_sims == 2 and loaded.n_players == 4
    assert loaded.config["draft_position"] == 5
    assert loaded.config["scoring_format"] == "full_ppr"
    assert loaded.metadata["note"] == "smoke"
    assert loaded.mu[2] == pytest.approx(3.0)


def test_undrafted_sentinel_survives(tmp_path):
    # int16 round-tripping must not mangle 999 -- every availability query
    # depends on undrafted being GREATER than any real pick.
    path = save_picks_matrix(tmp_path / "u.npz", make_picks(), make_config(),
                             ["1", "2", "3", "4"])
    loaded = load_picks_matrix(path)
    assert (loaded.picks == UNDRAFTED).sum() == 2


def test_refuses_to_save_mismatched_player_ids(tmp_path):
    # Caught at WRITE time, because at read time it's unrecoverable.
    with pytest.raises(ValueError, match="uninterpretable"):
        save_picks_matrix(tmp_path / "bad.npz", make_picks(), make_config(),
                          ["only", "three", "ids"])


def test_column_for_finds_the_right_player(tmp_path):
    path = save_picks_matrix(tmp_path / "c.npz", make_picks(), make_config(),
                             ["101", "102", "103", "104"])
    loaded = load_picks_matrix(path)

    assert loaded.column_for("103") == 2
    assert loaded.picks[0, loaded.column_for("104")] == UNDRAFTED


def test_column_for_raises_on_unknown_player(tmp_path):
    # A player beyond the pool cap isn't in the run. Returning something
    # plausible would be far worse than raising.
    path = save_picks_matrix(tmp_path / "d.npz", make_picks(), make_config(),
                             ["101", "102", "103", "104"])
    with pytest.raises(KeyError, match="not in this simulation"):
        load_picks_matrix(path).column_for("999999")


def test_load_rejects_archive_without_player_ordering(tmp_path):
    # Hand-build an archive with the matrix but no ids -- the exact file that
    # would otherwise load fine and produce confidently wrong answers.
    path = tmp_path / "orphan.npz"
    np.savez_compressed(path, picks=make_picks())

    with pytest.raises(KeyError, match="player ordering"):
        load_picks_matrix(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_picks_matrix(tmp_path / "nope.npz")


def test_path_changes_when_the_simulation_would_change():
    # A change to what the simulation consumes must produce a cache MISS, not a
    # stale hit. Settings that only affect DERIVED numbers (draft_position,
    # starting_slots) deliberately keep the same path -- they're recomputed on
    # load, so a stale value can't survive, and re-simulating for them would
    # produce a byte-identical matrix. See DraftConfig.fingerprint.
    base = artifact_path("data/sim", "draft1", make_config())

    assert base != artifact_path("data/sim", "draft1", make_config(num_teams=10))
    assert base != artifact_path("data/sim", "draft1", make_config(num_rounds=16))
    assert base == artifact_path("data/sim", "draft1", make_config())
    assert base == artifact_path("data/sim", "draft1", make_config(draft_position=6))


def test_matches_table_is_order_sensitive(tmp_path):
    import pandas as pd

    path = save_picks_matrix(tmp_path / "m.npz", make_picks(), make_config(),
                             ["101", "102", "103", "104"])
    loaded = load_picks_matrix(path)

    same = pd.DataFrame({"ffc_player_id": [101, 102, 103, 104]})
    assert matches_table(loaded, same)

    # Same players, different order -- every column would refer to the wrong
    # person, so this must NOT be treated as a match.
    reordered = pd.DataFrame({"ffc_player_id": [102, 101, 103, 104]})
    assert not matches_table(loaded, reordered)

    shorter = pd.DataFrame({"ffc_player_id": [101, 102, 103]})
    assert not matches_table(loaded, shorter)

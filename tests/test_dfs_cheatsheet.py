"""Tests for the Cheat Sheet's column catalogue and its slate.

The page has no opinion about any statistic -- it draws whatever the catalogue
declares -- so the catalogue is where mistakes land. Three of them would be
quiet:

1. A COLUMN DECLARED TWICE, which produces a duplicated column in the table and
   an ambiguous checkbox key.
2. A DEFAULT THAT IS NOT IN THE CATALOGUE, which silently shows nothing.
3. THE WRONG SCALE, which is how a share of 0.24 becomes 24% -- or how an
   already-multiplied 40.6% becomes 4062%.
"""

import pandas as pd
import pytest

from presentation.dfs_cheatsheet import (
    DEFAULT_COLUMNS, GROUPS, IDENTITY, SLATE_DEFAULTS, SLATE_GROUP, build,
    columns_by_field, every_column,
)
from services.dfs_player_service import slate


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def test_no_column_is_declared_twice():
    # A repeat would draw the column twice and give two checkboxes the same key.
    fields = [column.field for column in every_column()]
    assert len(fields) == len(set(fields))


def test_the_identity_columns_are_not_also_optional():
    # They are always shown, so offering them as checkboxes would let somebody
    # ask for a column they already have.
    optional = {column.field for column in every_column()}
    assert not optional & {column.field for column in IDENTITY}


def test_every_default_is_a_column_that_exists():
    # A default naming a column the catalogue does not have would silently show
    # nothing, and the page would look like it had ignored the setting.
    assert set(DEFAULT_COLUMNS) <= set(columns_by_field())


def test_the_default_set_is_deliberately_small():
    # The Draft Runner console reached fourteen columns and got harder to read.
    # Everything else here is one checkbox away.
    assert len(DEFAULT_COLUMNS) <= 8


def test_every_column_has_a_label_and_a_format_decision():
    for column in every_column():
        assert column.label
        # `format` may be None, which means "leave it as text" -- but the field
        # and label must always be there.
        assert column.field


def test_only_fraction_columns_carry_a_scale():
    # The sources disagree: `snap_share` arrives as a fraction, `aggressiveness`
    # arrives already multiplied out. Scaling the wrong one turns 40.6% into
    # 4062%.
    scaled = {column.field for column in every_column() if column.scale != 1.0}
    assert scaled == {"snap_share", "target_share", "air_yards_share",
                      "receiving_drop_pct",
                      # The slate's trailing-form versions of the same two.
                      "form_snap_share", "form_target_share"}


def test_the_groups_are_not_empty():
    for name, columns in GROUPS.items():
        assert columns, name


# ---------------------------------------------------------------------------
# Building the table
# ---------------------------------------------------------------------------


def frame_of(**columns):
    """A slate-shaped table with identity columns filled in."""
    rows = {"name": ["A"], "position": ["WR"], "team": ["SEA"],
            "opponent": ["SF"]}
    rows.update(columns)
    return pd.DataFrame(rows)


def test_identity_columns_come_first_and_are_never_optional():
    table, columns = build(frame_of(total_fantasy_points=[12.0]),
                           ["total_fantasy_points"])
    assert list(table.columns)[:4] == ["name", "position", "team", "opponent"]


def test_columns_appear_in_catalogue_order_not_tick_order():
    # So the table's shape stays familiar however somebody arrives at it.
    frame = frame_of(total_fantasy_points=[12.0], targets=[8], snap_share=[0.9])
    table, _ = build(frame, ["targets", "snap_share", "total_fantasy_points"])

    order = [c.field for c in every_column() if c.field in
             {"targets", "snap_share", "total_fantasy_points"}]
    assert list(table.columns)[4:] == order


def test_a_fraction_is_turned_into_a_percentage():
    table, _ = build(frame_of(snap_share=[0.85]), ["snap_share"])
    assert table["snap_share"].iloc[0] == pytest.approx(85.0)


def test_an_already_multiplied_column_is_left_alone():
    # The trap the scale field exists for.
    table, _ = build(frame_of(avg_intended_air_yards=[11.4]),
                     ["avg_intended_air_yards"])
    assert table["avg_intended_air_yards"].iloc[0] == pytest.approx(11.4)


def test_a_column_the_data_does_not_have_is_skipped():
    # A source can be unavailable for a season, and its columns go with it.
    table, columns = build(frame_of(total_fantasy_points=[12.0]),
                           ["total_fantasy_points", "avg_separation"])
    assert "avg_separation" not in table.columns
    assert "avg_separation" not in {c.field for c in columns}


def test_choosing_nothing_still_leaves_the_identity_columns():
    table, _ = build(frame_of(total_fantasy_points=[12.0]), [])
    assert list(table.columns) == ["name", "position", "team", "opponent"]


def test_the_source_frame_is_not_edited():
    # The scaling would otherwise double every time the page re-ran.
    frame = frame_of(snap_share=[0.85])
    build(frame, ["snap_share"])
    assert frame["snap_share"].iloc[0] == 0.85


# ---------------------------------------------------------------------------
# The slate itself
# ---------------------------------------------------------------------------


def player_weeks_frame(rows):
    """A `player_weeks`-shaped table, filled out around what each row states."""
    frame = pd.DataFrame(rows)
    defaults = {"canonical_id": "P1", "name": "A", "position": "WR",
                "team": "SEA", "opponent": "SF", "season": 2024, "week": 1,
                "offense_snaps": 40, "total_fantasy_points": 10.0,
                "total_fantasy_points_exp": 8.0}
    for column, value in defaults.items():
        if column not in frame.columns:
            frame[column] = value
        else:
            frame[column] = frame[column].fillna(value)
    return frame


def test_a_slate_is_one_week_only():
    # Totalling several weeks would answer the season-long question the Basic
    # Plots page already answers better.
    frame = player_weeks_frame([{"week": 1}, {"week": 2}, {"week": 3}])
    assert len(slate(frame, 2024, 2)) == 1


def test_the_gap_between_scored_and_expected_is_added():
    frame = player_weeks_frame([{"total_fantasy_points": 22.0,
                                 "total_fantasy_points_exp": 15.0}])
    assert slate(frame, 2024, 1)["points_gap"].iloc[0] == pytest.approx(7.0)


def test_the_slate_is_sorted_by_points_scored():
    frame = player_weeks_frame([
        {"canonical_id": "LOW", "name": "Low", "total_fantasy_points": 4.0},
        {"canonical_id": "HIGH", "name": "High", "total_fantasy_points": 30.0},
    ])
    assert list(slate(frame, 2024, 1)["name"]) == ["High", "Low"]


def test_the_snap_floor_hides_players_who_barely_appeared():
    frame = player_weeks_frame([
        {"canonical_id": "STARTER", "name": "Starter", "offense_snaps": 55},
        {"canonical_id": "CAMEO", "name": "Cameo", "offense_snaps": 3},
    ])
    assert list(slate(frame, 2024, 1, minimum_snaps=8)["name"]) == ["Starter"]


def test_positions_and_teams_can_both_be_narrowed():
    frame = player_weeks_frame([
        {"canonical_id": "A", "name": "A", "position": "WR", "team": "SEA"},
        {"canonical_id": "B", "name": "B", "position": "RB", "team": "SEA"},
        {"canonical_id": "C", "name": "C", "position": "WR", "team": "SF"},
    ])
    assert list(slate(frame, 2024, 1, positions=["WR"])["name"]) == ["A", "C"]
    assert list(slate(frame, 2024, 1, teams=["SEA"])["name"]) == ["A", "B"]


def test_an_empty_slate_still_carries_the_gap_column():
    frame = player_weeks_frame([{"week": 1}])
    empty = slate(frame, 2024, 17)
    assert empty.empty
    assert "points_gap" in empty.columns


def test_the_slate_defaults_are_real_slate_columns():
    # A default naming a column outside the slate group would show nothing when
    # a slate is loaded, and the page would look like it ignored the setting.
    slate_fields = {column.field for column in GROUPS[SLATE_GROUP]}
    assert set(SLATE_DEFAULTS) <= slate_fields


def test_the_slate_default_set_is_also_short():
    assert len(SLATE_DEFAULTS) <= 8

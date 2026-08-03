"""Tests for blending three analysts' projections into one number.

Offline: these exercise the blend logic against stub adapters and repos, with no
Mongo and no nflreadpy.

The collision test earns its place -- `bye_week` is numeric but is an IDENTITY
column, so it initially landed in both the numeric aggregation and the label
frame and came out as bye_week_x / bye_week_y. Nothing raised; the column simply
vanished under the name every consumer expected.
"""

import numpy as np
import pandas as pd
import pytest

from scoring import ScoringFormat
from services.projections_service import ProjectionsService

STAT_COLUMNS = [
    "passing_yards", "passing_tds", "interceptions",
    "rushing_attempts", "rushing_yards", "rushing_tds",
    "receptions", "receiving_yards", "receiving_tds", "fumbles_lost",
]


def player_row(name, position, receiving_yards, **overrides):
    row = {c: 0 for c in STAT_COLUMNS}
    row.update({"name": name, "team": "XXX", "bye_week": 7, "position": position,
                "rank": 1, "receiving_yards": receiving_yards})
    row.update(overrides)
    return row


class StubAdapter:
    """Stands in for FfbProjectionsAdapter with hand-written per-analyst rows."""

    def __init__(self, rows_by_analyst):
        self._rows = rows_by_analyst

    @property
    def analysts(self):
        return list(self._rows)

    def load(self, analyst):
        return pd.DataFrame(self._rows[analyst])

    def load_all(self):
        return pd.concat(
            [self.load(a).assign(analyst=a) for a in self._rows],
            ignore_index=True,
        )


class StubIdentity:
    """Resolves names to canonical ids by a fixed map."""

    def __init__(self, mapping):
        self._mapping = mapping

    def resolve_many_with_fallback(self, source, names, positions=None):
        return names.map(self._mapping)

    def unresolved_with_fallback(self, source, names, positions=None):
        return names[~names.isin(self._mapping)].drop_duplicates().tolist()


class StubRoster:
    def __init__(self, ids):
        self._ids = set(ids)

    def canonical_ids(self):
        return self._ids


def make_service(rows_by_analyst, mapping):
    return ProjectionsService(
        StubAdapter(rows_by_analyst), StubIdentity(mapping), StubRoster(mapping.values())
    )


# --------------------------------------------------------------------------

def test_blend_averages_the_analysts():
    # 100 / 130 / 160 receiving yards -> 10 / 13 / 16 PPR points from yards,
    # plus 0 receptions. The blend must be the mean, 13.
    service = make_service(
        {
            "andy": [player_row("A Player", "WR", 100)],
            "mike": [player_row("A Player", "WR", 130)],
            "jason": [player_row("A Player", "WR", 160)],
        },
        {"A Player": "c1"},
    )

    blended = service.get_own_projections()
    column = "fantasy_points_full_ppr_season"
    assert len(blended) == 1
    assert blended[column].iloc[0] == pytest.approx(13.0)
    assert blended["n_analysts"].iloc[0] == 3


def test_blend_records_low_high_and_spread():
    service = make_service(
        {
            "andy": [player_row("A Player", "WR", 100)],
            "mike": [player_row("A Player", "WR", 130)],
            "jason": [player_row("A Player", "WR", 160)],
        },
        {"A Player": "c1"},
    )

    row = service.get_own_projections().iloc[0]
    base = "fantasy_points_full_ppr_season"
    assert row[f"{base}_low"] == pytest.approx(10.0)
    assert row[f"{base}_high"] == pytest.approx(16.0)
    # Range, not standard deviation -- with three observations the range is the
    # question actually being asked.
    assert row[f"{base}_spread"] == pytest.approx(6.0)


def test_blend_averages_over_whoever_rated_the_player():
    # Only two analysts rate the second player. He must be averaged over TWO,
    # not treated as having a zero projection from the third -- that would bury
    # every deep player one analyst happened to skip.
    service = make_service(
        {
            "andy": [player_row("Both", "WR", 100), player_row("Deep Guy", "WR", 60)],
            "mike": [player_row("Both", "WR", 200), player_row("Deep Guy", "WR", 80)],
            "jason": [player_row("Both", "WR", 300)],
        },
        {"Both": "c1", "Deep Guy": "c2"},
    )

    blended = service.get_own_projections().set_index("canonical_id")
    column = "fantasy_points_full_ppr_season"

    assert blended.loc["c1", "n_analysts"] == 3
    assert blended.loc["c1", column] == pytest.approx(20.0)      # mean of 10/20/30
    assert blended.loc["c2", "n_analysts"] == 2
    assert blended.loc["c2", column] == pytest.approx(7.0)       # mean of 6/8, NOT 4.67


def test_identity_columns_survive_the_blend():
    # REGRESSION TEST. bye_week is numeric but is an identity column. It first
    # landed in both the numeric aggregation and the label frame, and the merge
    # renamed both to bye_week_x / bye_week_y -- so the column consumers expect
    # silently ceased to exist, with nothing raising.
    service = make_service(
        {
            "andy": [player_row("A Player", "WR", 100)],
            "mike": [player_row("A Player", "WR", 130)],
        },
        {"A Player": "c1"},
    )

    blended = service.get_own_projections()
    for column in ("canonical_id", "name", "team", "position", "bye_week"):
        assert column in blended.columns, f"{column} lost in the blend"
    assert not [c for c in blended.columns if c.endswith(("_x", "_y"))]
    assert blended["bye_week"].iloc[0] == 7


def test_single_analyst_bypasses_the_blend():
    service = make_service(
        {
            "andy": [player_row("A Player", "WR", 100)],
            "mike": [player_row("A Player", "WR", 200)],
        },
        {"A Player": "c1"},
    )

    only_andy = service.get_own_projections("andy")
    assert only_andy["fantasy_points_full_ppr_season"].iloc[0] == pytest.approx(10.0)
    # Spread columns are a property of blending; a single analyst has no spread.
    assert "fantasy_points_full_ppr_season_spread" not in only_andy.columns


def test_disagreement_excludes_single_rated_players():
    # A "spread" computed from one opinion is zero by construction and would
    # rank alongside genuine consensus. Exclude rather than mislead.
    service = make_service(
        {
            "andy": [player_row("Rated Twice", "WR", 100), player_row("Rated Once", "WR", 50)],
            "mike": [player_row("Rated Twice", "WR", 300)],
        },
        {"Rated Twice": "c1", "Rated Once": "c2"},
    )

    report = service.disagreement(ScoringFormat.FULL_PPR)
    assert list(report["canonical_id"]) == ["c1"]
    assert report["fantasy_points_full_ppr_season_spread"].iloc[0] == pytest.approx(20.0)

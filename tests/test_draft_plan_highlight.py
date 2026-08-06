"""Tests for highlighting your saved draft plan inside the runner's console.

The failure this guards against is a quiet one. A draft plan stores DISPLAY
NAMES, taken from the roster service; the console shows names from the model
table, which come from FFC. The two sources spell people differently -- suffixes
and accents -- so matching them by name misses players silently, and a highlight
that is sometimes missing reads as "my plan was wrong" rather than "the match
failed". Both sides agree on canonical id, so that is what the matching uses.

The other trap is the round label. DraftPlanService builds labels like "3.04"
with its own snake arithmetic that does not know about third-round reversal, so
in a reversal league the pick-in-round half disagrees with the model. Matching on
the round half only is right either way.
"""

import pandas as pd
import pytest

from services.draft_runner_service import (
    planned_canonical_ids, planned_names_for_round, round_of_pick,
)


@pytest.fixture
def roster():
    """As RosterService.roster() returns it -- names and ids side by side."""
    return pd.DataFrame({
        "canonical_id": ["id0", "id1", "id2", "id3"],
        "display_name": ["Kenneth Walker III", "Bijan Robinson",
                         "Eddy Piñeiro", "Puka Nacua"],
        "tier": [2, 1, 8, 1],
    })


# --------------------------------------------------------------------------
# Which round are we in
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pick,teams,expected", [
    (1, 12, 1), (12, 12, 1), (13, 12, 2), (21, 12, 2), (24, 12, 2), (25, 12, 3),
    (1, 4, 1), (5, 4, 2),
])
def test_round_of_pick(pick, teams, expected):
    assert round_of_pick(pick, teams) == expected


# --------------------------------------------------------------------------
# Reading the plan
# --------------------------------------------------------------------------

def test_a_rounds_targets_are_pooled_across_positions():
    # One round's plan is spread over four entries, one per position tab.
    plan = {("3.04", "RB"): ["A", "B"],
            ("3.04", "WR"): ["C"],
            ("4.09", "RB"): ["D"]}
    assert planned_names_for_round(plan, 3) == {"A", "B", "C"}


def test_other_rounds_are_left_alone():
    plan = {("3.04", "RB"): ["A"], ("4.09", "RB"): ["D"]}
    assert planned_names_for_round(plan, 4) == {"D"}
    assert planned_names_for_round(plan, 9) == set()


def test_the_pick_half_of_the_label_is_ignored():
    # DraftPlanService's labels do not account for third-round reversal, so the
    # pick-in-round half can disagree with the model's own numbering. The round
    # half is right either way, and you own one pick per round.
    plan = {("3.04", "RB"): ["A"], ("3.09", "WR"): ["B"]}
    assert planned_names_for_round(plan, 3) == {"A", "B"}


def test_an_empty_or_missing_plan_is_not_an_error():
    assert planned_names_for_round({}, 3) == set()
    assert planned_names_for_round(None, 3) == set()


# --------------------------------------------------------------------------
# Resolving names to ids
# --------------------------------------------------------------------------

def test_planned_names_resolve_to_canonical_ids(roster):
    plan = {("2.04", "RB"): ["Bijan Robinson"], ("2.04", "WR"): ["Puka Nacua"]}
    ids, unresolved = planned_canonical_ids(plan, 2, roster)

    assert ids == {"id1", "id3"}
    assert unresolved == []


def test_a_name_the_roster_does_not_carry_is_reported_not_dropped(roster):
    # Swallowing it would show fewer stars than you planned, which reads as the
    # plan being wrong rather than the match failing.
    plan = {("2.04", "RB"): ["Bijan Robinson", "Somebody Retired"]}
    ids, unresolved = planned_canonical_ids(plan, 2, roster)

    assert ids == {"id1"}
    assert unresolved == ["Somebody Retired"]


def test_matching_is_exact_on_the_rosters_own_spelling(roster):
    # This is why the resolution goes through the roster at all. The console
    # shows FFC's "Kenneth Walker"; the plan stores the roster's "Kenneth
    # Walker III". Resolving the PLAN side against the roster gets the id, and
    # the id is what the console matches on.
    ids, unresolved = planned_canonical_ids(
        {("2.04", "RB"): ["Kenneth Walker III"]}, 2, roster)
    assert ids == {"id0"}

    # The FFC spelling is NOT what the plan stores, so it does not resolve --
    # and it is reported rather than silently missing.
    ids, unresolved = planned_canonical_ids(
        {("2.04", "RB"): ["Kenneth Walker"]}, 2, roster)
    assert ids == set()
    assert unresolved == ["Kenneth Walker"]


def test_an_empty_plan_resolves_to_nothing(roster):
    assert planned_canonical_ids({}, 2, roster) == (set(), [])


def test_only_the_asked_for_round_is_resolved(roster):
    plan = {("2.04", "RB"): ["Bijan Robinson"],
            ("5.04", "WR"): ["Puka Nacua"]}
    ids, _ = planned_canonical_ids(plan, 2, roster)
    assert ids == {"id1"}


def test_a_roster_row_with_no_id_does_not_become_a_match():
    # Team defenses carry no canonical id. They never appear in a plan, but a
    # blank must not resolve to something truthy if one ever did.
    roster = pd.DataFrame({"canonical_id": [None], "display_name": ["Ravens D/ST"]})
    ids, unresolved = planned_canonical_ids(
        {("2.04", "DST"): ["Ravens D/ST"]}, 2, roster)
    assert ids == set()
    assert unresolved == ["Ravens D/ST"]

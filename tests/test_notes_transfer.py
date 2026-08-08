"""Tests for copying your own notes and tags between saved leagues.

This is the one feature that writes over work the user did by hand, so the
properties that matter are mostly about NOT losing things:

1. NOTHING IS OVERWRITTEN. Where both leagues have something for the same
   player, tags are combined and both notes survive.
2. COPYING TWICE IS HARMLESS. Otherwise the obvious thing to do -- add one tag in
   the source, run the copy again -- appends the same paragraph a second time.
3. A PREVIEW WRITES NOTHING, and says exactly what the real copy would do. A
   preview that disagrees with the outcome is worse than none.
4. EMPTY MARKINGS ARE NOT COPIED. Opening the editor and saving without typing
   leaves a row with no tags and no note; nine of ten in a real draft were that.
"""

import pytest

from registry import MARKING_CATEGORIES
from services.notes_transfer_service import (
    ADDED, MERGED, UNCHANGED, NotesTransferService, merge_categories,
    merge_notes,
)


class FakeStore:
    """Stands in for a markings or team-notes service, holding rows in a list."""

    def __init__(self, rows=(), key="canonical_id"):
        self.rows = [dict(r) for r in rows]
        self.key = key
        self.writes = []

    def all_for_draft(self, draft_id):
        return [dict(r) for r in self.rows if r["draft_id"] == draft_id]

    def _store(self, draft_id, key_value, fields):
        self.writes.append((draft_id, key_value, fields))
        for row in self.rows:
            if row["draft_id"] == draft_id and row[self.key] == key_value:
                row.update(fields)
                return
        self.rows.append({"draft_id": draft_id, self.key: key_value, **fields})

    # PlayerMarkingsService.save(draft_id, canonical_id, categories, notes)
    def save(self, draft_id, key_value, categories=None, notes=None):
        if categories is None:                       # TeamNotesService.save
            self._store(draft_id, key_value, {"notes": notes})
        else:
            self._store(draft_id, key_value,
                        {"categories": categories, "notes": notes})


class FakeTeams(FakeStore):
    """A team-notes service: save takes (draft_id, team_abbr, notes)."""

    def __init__(self, rows=()):
        super().__init__(rows, key="team_abbr")

    def save(self, draft_id, team_abbr, notes):
        self._store(draft_id, team_abbr, {"notes": notes})


def marking(draft, player, categories=(), notes=""):
    return {"draft_id": draft, "canonical_id": player,
            "categories": list(categories), "notes": notes}


def team_note(draft, team, notes):
    return {"draft_id": draft, "team_abbr": team, "notes": notes}


def service(markings=(), teams=()):
    return NotesTransferService(FakeStore(markings), FakeTeams(teams))


# ---------------------------------------------------------------------------
# Combining tags
# ---------------------------------------------------------------------------


def test_tags_from_both_leagues_survive():
    assert set(merge_categories(["Safe"], ["Love"])) == {"Safe", "Love"}


def test_a_tag_in_both_is_not_repeated():
    assert merge_categories(["Safe"], ["Safe"]) == ["Safe"]


def test_tags_come_back_in_a_predictable_order():
    # Two players tagged the same way should list them the same way, whichever
    # league each tag arrived from.
    one = merge_categories(["Love"], ["Safe"])
    other = merge_categories(["Safe"], ["Love"])
    assert one == other
    assert one == [t for t in MARKING_CATEGORIES if t in set(one)]


def test_a_tag_no_longer_in_the_registry_is_kept():
    # Removing a category from the registry should not silently delete it from
    # everything a user already tagged.
    merged = merge_categories(["Retired Tag"], ["Safe"])
    assert "Retired Tag" in merged and "Safe" in merged


def test_empty_inputs_are_handled():
    assert merge_categories(None, None) == []
    assert merge_categories([], ["Safe"]) == ["Safe"]


# ---------------------------------------------------------------------------
# Combining notes
# ---------------------------------------------------------------------------


def test_a_note_arriving_where_there_is_none_is_used_as_is():
    assert merge_notes("", "his handcuff", "2025 Home") == "his handcuff"


def test_both_notes_are_kept_and_the_source_is_named():
    merged = merge_notes("mine", "theirs", "2025 Home")
    assert "mine" in merged and "theirs" in merged
    assert "2025 Home" in merged


def test_copying_the_same_note_twice_does_not_repeat_it():
    # THE IDEMPOTENCE TEST. Without it, adding one tag in the source and
    # re-running the copy appends the whole paragraph again.
    once = merge_notes("mine", "theirs", "2025 Home")
    twice = merge_notes(once, "theirs", "2025 Home")
    assert once == twice
    assert twice.count("theirs") == 1


def test_an_empty_source_note_leaves_the_target_alone():
    assert merge_notes("mine", "", "2025 Home") == "mine"
    assert merge_notes("mine", None, "2025 Home") == "mine"


def test_whitespace_around_a_note_is_not_treated_as_content():
    assert merge_notes("mine", "   ", "2025 Home") == "mine"


# ---------------------------------------------------------------------------
# Copying markings
# ---------------------------------------------------------------------------


def test_a_player_the_target_has_never_heard_of_is_added():
    svc = service([marking("A", "p1", ["Love"], "my guy")])
    report = svc.copy("A", "B")

    assert report.counts("player")[ADDED] == 1
    stored = svc._markings.all_for_draft("B")
    assert stored[0]["categories"] == ["Love"]
    assert stored[0]["notes"] == "my guy"


def test_a_player_in_both_is_merged_not_overwritten():
    svc = service([marking("A", "p1", ["Love"], "from A"),
                   marking("B", "p1", ["Safe"], "from B")])
    svc.copy("A", "B", source_label="League A")

    stored = svc._markings.all_for_draft("B")[0]
    assert set(stored["categories"]) == {"Love", "Safe"}
    assert "from A" in stored["notes"] and "from B" in stored["notes"]


def test_running_the_copy_twice_changes_nothing_the_second_time():
    svc = service([marking("A", "p1", ["Love"], "from A"),
                   marking("B", "p1", ["Safe"], "from B")])
    svc.copy("A", "B", source_label="League A")
    after_one = svc._markings.all_for_draft("B")[0]["notes"]

    second = svc.copy("A", "B", source_label="League A")

    assert svc._markings.all_for_draft("B")[0]["notes"] == after_one
    assert second.counts("player")[UNCHANGED] == 1
    assert second.touched == 0


def test_an_empty_marking_is_not_copied():
    # Nine of ten markings in a real draft were exactly this -- the editor
    # opened and saved without anything typed.
    svc = service([marking("A", "p1", [], ""),
                   marking("A", "p2", ["Love"], "")])
    report = svc.copy("A", "B")

    assert [c.key for c in report.of_kind("player")] == ["p2"]
    assert len(svc._markings.all_for_draft("B")) == 1


def test_a_player_whose_tags_are_already_there_is_left_alone():
    svc = service([marking("A", "p1", ["Love"], ""),
                   marking("B", "p1", ["Love"], "")])
    report = svc.copy("A", "B")

    assert report.counts("player")[UNCHANGED] == 1
    assert svc._markings.writes == []          # nothing written at all


# ---------------------------------------------------------------------------
# Copying team notes
# ---------------------------------------------------------------------------


def test_team_notes_are_copied_too():
    svc = service(teams=[team_note("A", "KC", "fast offence")])
    report = svc.copy("A", "B")

    assert report.counts("team")[ADDED] == 1
    assert svc._team_notes.all_for_draft("B")[0]["notes"] == "fast offence"


def test_a_team_note_in_both_keeps_both():
    svc = service(teams=[team_note("A", "KC", "from A"),
                         team_note("B", "KC", "from B")])
    svc.copy("A", "B", source_label="League A")

    stored = svc._team_notes.all_for_draft("B")[0]["notes"]
    assert "from A" in stored and "from B" in stored


def test_an_empty_team_note_is_not_copied():
    svc = service(teams=[team_note("A", "KC", "   ")])
    assert svc.copy("A", "B").counts("team")[ADDED] == 0


# ---------------------------------------------------------------------------
# The preview
# ---------------------------------------------------------------------------


def test_a_preview_writes_nothing():
    svc = service([marking("A", "p1", ["Love"], "my guy")],
                  [team_note("A", "KC", "fast")])
    report = svc.preview("A", "B")

    assert not report.applied
    assert svc._markings.writes == []
    assert svc._team_notes.writes == []
    assert svc._markings.all_for_draft("B") == []


def test_a_preview_says_exactly_what_the_copy_will_do():
    # The two are the same code with one flag different, and this is the test
    # that keeps them that way.
    rows = [marking("A", "p1", ["Love"], "from A"),
            marking("A", "p2", ["Safe"], ""),
            marking("B", "p1", ["Safe"], "from B")]

    previewed = service(rows).preview("A", "B", source_label="L")
    applied = service(rows).copy("A", "B", source_label="L")

    assert [(c.key, c.action, c.detail) for c in previewed.changes] == \
           [(c.key, c.action, c.detail) for c in applied.changes]


def test_the_report_counts_what_it_touched():
    svc = service([marking("A", "p1", ["Love"], ""),
                   marking("A", "p2", ["Safe"], ""),
                   marking("B", "p1", ["Love"], "")])
    report = svc.preview("A", "B")

    assert report.counts("player") == {ADDED: 1, MERGED: 0, UNCHANGED: 1}
    assert report.touched == 1


def test_player_names_are_used_in_the_report_when_supplied():
    svc = service([marking("A", "p1", ["Love"], "")])
    report = svc.preview("A", "B", names={"p1": "Player One"})
    assert report.of_kind("player")[0].label == "Player One"


def test_an_unknown_player_falls_back_to_his_id():
    svc = service([marking("A", "p1", ["Love"], "")])
    assert svc.preview("A", "B").of_kind("player")[0].label == "p1"


# ---------------------------------------------------------------------------
# Refusals and edges
# ---------------------------------------------------------------------------


def test_copying_a_league_into_itself_is_refused():
    # It would merge every note with a copy of itself, which is nonsense and
    # would look like corruption afterwards.
    with pytest.raises(ValueError, match="itself"):
        service([marking("A", "p1", ["Love"], "x")]).copy("A", "A")


def test_copying_from_an_empty_league_does_nothing():
    report = service().copy("A", "B")
    assert report.changes == ()
    assert report.touched == 0


def test_a_row_with_no_player_id_is_ignored():
    svc = service([{"draft_id": "A", "canonical_id": None,
                    "categories": ["Love"], "notes": "x"}])
    assert svc.copy("A", "B").changes == ()

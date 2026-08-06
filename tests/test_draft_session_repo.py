"""Tests for draft session storage.

No database is involved. The repository takes its db/documents.py helpers as an
argument, so these run against a small in-memory stand-in -- which keeps the
whole suite runnable with no MONGODB_URI, as it is today.

The fake returns DEEP COPIES from every read, because real MongoDB does too: what
comes back is deserialized from the wire, not the stored object. Returning the
stored dictionary instead would let a test mutate storage by accident and pass,
while the same code against a real database silently did nothing.
"""

import copy

import pytest

from draft_model.config import DraftConfig
from repositories.draft_session_repo import DraftSessionRepo
from scoring import ScoringFormat
from services.draft_runner_service import DraftState, state_from_session


class FakeDocuments:
    """An in-memory stand-in for db/documents.py.

    Implements just the five functions the repository calls, with the same
    signatures and the same semantics -- notably that `upsert` MERGES fields into
    an existing row rather than replacing it.
    """

    def __init__(self):
        self.rows = []
        self.indexes = []

    def _matches(self, row, filter):
        return all(row.get(key) == value for key, value in (filter or {}).items())

    def find_one(self, collection_name, filter):
        for row in self.rows:
            if self._matches(row, filter):
                return copy.deepcopy(row)
        return None

    def find_all(self, collection_name, filter=None):
        return [copy.deepcopy(r) for r in self.rows if self._matches(r, filter)]

    def upsert(self, collection_name, filter, doc):
        for row in self.rows:
            if self._matches(row, filter):
                row.update(copy.deepcopy(doc))
                return
        inserted = dict(filter)
        inserted.update(copy.deepcopy(doc))
        self.rows.append(inserted)

    def delete(self, collection_name, filter):
        for i, row in enumerate(self.rows):
            if self._matches(row, filter):
                del self.rows[i]
                return

    def ensure_index(self, collection_name, fields, unique=False, name=None):
        self.indexes.append((tuple(fields), unique))
        return name or "_".join(fields)


@pytest.fixture
def repo():
    """A repository backed by a fresh in-memory store."""
    return DraftSessionRepo(collection_name="test_sessions", documents=FakeDocuments())


def make_config(num_teams=4, num_rounds=3):
    return DraftConfig(year=2026, num_teams=num_teams, num_rounds=num_rounds,
                       draft_position=1, scoring_format=ScoringFormat.FULL_PPR)


# --------------------------------------------------------------------------
# Creating and loading
# --------------------------------------------------------------------------

def test_a_new_session_starts_empty(repo):
    session = repo.create("abc", "sim", "Practice 1", seed=42)
    assert session["picks"] == []
    assert session["mode"] == "sim"
    assert session["seed"] == 42
    assert session["session_id"]


def test_a_session_can_be_loaded_back_by_id(repo):
    created = repo.create("abc", "sim", "Practice 1")
    assert repo.get(created["session_id"])["name"] == "Practice 1"


def test_an_unknown_session_is_none_rather_than_an_error(repo):
    # A stale id in session state gets you here. It is a normal state.
    assert repo.get("no-such-session") is None


def test_an_unrecognised_mode_is_refused(repo):
    with pytest.raises(ValueError, match="mode must be one of"):
        repo.create("abc", "scrimmage", "Practice 1")


def test_sessions_get_a_random_seed_when_none_is_given(repo):
    # Two practice drafts must face different opponents, or practising twice
    # tells you nothing new the second time.
    seeds = {repo.create("abc", "sim", f"Practice {i}")["seed"] for i in range(5)}
    assert len(seeds) > 1


# --------------------------------------------------------------------------
# Saving picks
# --------------------------------------------------------------------------

def test_picks_survive_a_round_trip(repo):
    session = repo.create("abc", "sim", "Practice 1", seed=7)
    state = state_from_session(session, make_config())
    state.make_pick("id0")
    state.make_pick("id1")

    repo.save_picks(session["session_id"], state.picks)
    reloaded = state_from_session(repo.get(session["session_id"]), make_config())

    assert reloaded.state_key == state.state_key
    assert reloaded.current_pick == 3
    assert reloaded.seed == 7


def test_a_rewind_is_stored_as_a_shrink(repo):
    # The reason save_picks writes the WHOLE log. An append-only write would
    # leave the discarded picks in the database, and the next reload would
    # resurrect a draft the user had already undone.
    session = repo.create("abc", "sim", "Practice 1")
    state = state_from_session(session, make_config())
    for i in range(5):
        state.make_pick(f"id{i}")
    repo.save_picks(session["session_id"], state.picks)

    state.rewind_to(3)
    repo.save_picks(session["session_id"], state.picks)

    assert len(repo.get(session["session_id"])["picks"]) == 2


def test_saving_does_not_disturb_the_rest_of_the_session(repo):
    session = repo.create("abc", "sim", "Practice 1", seed=99)
    repo.save_picks(session["session_id"], [{"pick": 1, "team": 1,
                                             "canonical_id": "id0",
                                             "source": "user"}])
    stored = repo.get(session["session_id"])
    assert stored["seed"] == 99
    assert stored["name"] == "Practice 1"
    assert stored["created_at"] == session["created_at"]


def test_a_loaded_state_does_not_write_through_to_storage(repo):
    # state_from_session copies the list. Without that, appending a pick would
    # mutate the dictionary the database handed back, and the difference between
    # "saved" and "not saved yet" would stop being real.
    session = repo.create("abc", "sim", "Practice 1")
    state = state_from_session(session, make_config())
    state.make_pick("id0")
    assert repo.get(session["session_id"])["picks"] == []


# --------------------------------------------------------------------------
# The live session
# --------------------------------------------------------------------------

def test_the_live_session_is_created_once_and_reused(repo):
    first = repo.get_or_create_live("abc")
    second = repo.get_or_create_live("abc")
    assert first["session_id"] == second["session_id"]
    assert first["mode"] == "live"


def test_opening_the_live_session_does_not_lose_its_picks(repo):
    live = repo.get_or_create_live("abc")
    repo.save_picks(live["session_id"], [{"pick": 1, "team": 1,
                                          "canonical_id": "id0",
                                          "source": "user"}])
    assert len(repo.get_or_create_live("abc")["picks"]) == 1


def test_the_live_session_cannot_be_deleted(repo):
    live = repo.get_or_create_live("abc")
    with pytest.raises(ValueError, match="refusing to delete"):
        repo.delete(live["session_id"])
    assert repo.get(live["session_id"]) is not None


# --------------------------------------------------------------------------
# Listing and deleting
# --------------------------------------------------------------------------

def test_listing_puts_the_live_session_first(repo):
    repo.create("abc", "sim", "Practice 1")
    repo.get_or_create_live("abc")
    repo.create("abc", "sim", "Practice 2")

    listed = repo.list_for_draft("abc")
    assert listed[0]["mode"] == "live"
    assert [s["name"] for s in listed[1:]] == ["Practice 1", "Practice 2"]


def test_sessions_belong_to_one_draft_only(repo):
    repo.create("abc", "sim", "Mine")
    repo.create("xyz", "sim", "Somebody else's")
    assert [s["name"] for s in repo.list_for_draft("abc")] == ["Mine"]


def test_a_draft_with_no_sessions_lists_nothing(repo):
    assert repo.list_for_draft("never-drafted") == []


def test_a_practice_session_can_be_deleted(repo):
    session = repo.create("abc", "sim", "Practice 1")
    repo.delete(session["session_id"])
    assert repo.get(session["session_id"]) is None


def test_deleting_a_session_that_is_already_gone_is_fine(repo):
    repo.delete("no-such-session")          # must not raise


# --------------------------------------------------------------------------
# Indexes
# --------------------------------------------------------------------------

def test_session_ids_are_unique_at_the_database_level(repo):
    # Enforced by the database rather than trusted to uuid4, so a duplicate is a
    # loud error instead of two drafts quietly sharing one record.
    repo.ensure_indexes()
    assert (("session_id",), True) in repo._documents.indexes


# --------------------------------------------------------------------------
# Renaming, and keeping practice sessions apart
# --------------------------------------------------------------------------

def test_a_session_can_be_renamed(repo):
    # The useful name for a practice run is rarely the one you thought of before
    # drafting a single player.
    session = repo.create("abc", "sim", "Practice 1")
    repo.rename(session["session_id"], "zero RB, pick 10")
    assert repo.get(session["session_id"])["name"] == "zero RB, pick 10"


def test_renaming_leaves_the_picks_and_seed_alone(repo):
    session = repo.create("abc", "sim", "Practice 1", seed=7)
    repo.save_picks(session["session_id"], [{"pick": 1, "team": 1,
                                             "player_id": "1",
                                             "canonical_id": "id0",
                                             "source": "user"}])
    repo.rename(session["session_id"], "renamed")

    stored = repo.get(session["session_id"])
    assert stored["seed"] == 7
    assert len(stored["picks"]) == 1


def test_a_blank_name_is_refused(repo):
    session = repo.create("abc", "sim", "Practice 1")
    with pytest.raises(ValueError, match="needs a name"):
        repo.rename(session["session_id"], "   ")


def test_practice_sessions_do_not_interfere_with_each_other(repo):
    # The whole point of named sims: practise repeatedly without any run
    # touching another.
    first = repo.create("abc", "sim", "Practice 1")
    second = repo.create("abc", "sim", "Practice 2")

    repo.save_picks(first["session_id"], [{"pick": 1, "team": 1,
                                           "player_id": "1",
                                           "canonical_id": "id0",
                                           "source": "user"}])

    assert len(repo.get(first["session_id"])["picks"]) == 1
    assert repo.get(second["session_id"])["picks"] == []


def test_practising_never_touches_the_live_draft(repo):
    # The failure that would actually hurt: a practice run writing over the
    # record of a real draft.
    live = repo.get_or_create_live("abc")
    repo.save_picks(live["session_id"], [{"pick": 1, "team": 1,
                                          "player_id": "1",
                                          "canonical_id": "id0",
                                          "source": "user"}])

    sim = repo.create("abc", "sim", "Practice 1")
    repo.save_picks(sim["session_id"], [])
    repo.delete(sim["session_id"])

    assert len(repo.get(live["session_id"])["picks"]) == 1


def test_resetting_a_sim_clears_only_its_picks(repo):
    # "Reset to pick 1" is just save_picks with an empty list.
    session = repo.create("abc", "sim", "Practice 1", seed=5)
    repo.save_picks(session["session_id"], [{"pick": 1, "team": 1,
                                             "player_id": "1",
                                             "canonical_id": "id0",
                                             "source": "user"}])
    repo.save_picks(session["session_id"], [])

    stored = repo.get(session["session_id"])
    assert stored["picks"] == []
    assert stored["seed"] == 5
    assert stored["name"] == "Practice 1"


def test_three_sims_and_a_live_draft_coexist(repo):
    # The Phase 7 acceptance test.
    repo.get_or_create_live("abc")
    for i in range(1, 4):
        repo.create("abc", "sim", f"Practice {i}")

    listed = repo.list_for_draft("abc")
    assert len(listed) == 4
    assert listed[0]["mode"] == "live"
    assert sum(1 for s in listed if s["mode"] == "sim") == 3
    assert len({s["session_id"] for s in listed}) == 4

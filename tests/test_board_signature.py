"""DraftSimService.board_signature must change whenever the loaded board would.

The signature is what the Draft Plan page uses as its cache key. If it fails to
move when a setting moves, the page serves a board built under the OLD settings
and every availability number on screen is confidently wrong -- which is exactly
the bug these tests exist to prevent from coming back.

The service is built with None for its three data services on purpose:
board_signature only reads the draft doc and the artifact directory, so nothing
here needs Mongo, FFC or projections.
"""

import pytest

from services.draft_sim_service import DraftSimService

# A saved draft as DraftService stores one: the fields DraftConfig.from_draft_doc
# reads, with scoring_format kept as its .value string.
BASE_DRAFT = {
    "draft_id": "abc123",
    "name": "Test League",
    "num_teams": 12,
    "num_rounds": 15,
    "draft_position": 4,
    "platform": "espn",
    "scoring_format": "full_ppr",
    "starting_slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1},
    "keepers": [],
    "roster_size": 16,
    "third_round_reversal": False,
}


@pytest.fixture
def service(tmp_path):
    """A service pointed at an empty temp directory, so no artifacts exist."""
    return DraftSimService(None, None, None, sim_dir=tmp_path)


def draft_with(**changes):
    """A copy of BASE_DRAFT with some fields replaced."""
    return {**BASE_DRAFT, **changes}


@pytest.mark.parametrize("field,value", [
    # Changes the simulation itself -- these also move config.fingerprint().
    ("platform", "yahoo"),
    ("num_teams", 10),
    ("num_rounds", 16),
    ("scoring_format", "half_ppr"),
    ("keepers", [{"team": 4, "round": 3, "canonical_id": "00-0034796"}]),
    ("third_round_reversal", True),
    # Deliberately EXCLUDED from the fingerprint because they don't change the
    # simulation -- but they do change the board that gets loaded, so the cache
    # key must still move for them.
    ("draft_position", 9),
    ("starting_slots", {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}),
    ("roster_size", 18),
])
def test_signature_changes_with_setting(service, field, value):
    before = service.board_signature(BASE_DRAFT, year=2026)
    after = service.board_signature(draft_with(**{field: value}), year=2026)
    assert before != after, f"changing {field} left the cache key unchanged"


def test_signature_is_stable_for_unchanged_settings(service):
    """Same draft, same key -- otherwise the cache would never hit at all."""
    first = service.board_signature(BASE_DRAFT, year=2026)
    second = service.board_signature(dict(BASE_DRAFT), year=2026)
    assert first == second


def test_signature_ignores_fields_the_model_never_reads(service):
    """Renaming a draft shouldn't throw away a 10,000-sim board."""
    renamed = draft_with(name="Renamed League")
    assert service.board_signature(renamed, year=2026) == \
           service.board_signature(BASE_DRAFT, year=2026)


def test_signature_changes_when_the_artifact_is_rewritten(service, tmp_path):
    """Re-running the sim under UNCHANGED settings overwrites the same filename.

    Nothing in the draft doc moves, so without the file's timestamp in the key
    the page would keep showing the previous run's numbers.
    """
    from draft_model.config import DraftConfig
    from draft_model.artifacts import artifact_path

    config = DraftConfig.from_draft_doc(BASE_DRAFT, year=2026)
    path = artifact_path(tmp_path, BASE_DRAFT["draft_id"], config)

    missing = service.board_signature(BASE_DRAFT, year=2026)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"pretend this is an npz")
    written = service.board_signature(BASE_DRAFT, year=2026)
    assert written != missing, "a newly saved simulation must invalidate the cache"

    # A second run a moment later writes the same path with a newer timestamp.
    import os
    os.utime(path, ns=(0, 2_000_000_000_000_000_000))
    rerun = service.board_signature(BASE_DRAFT, year=2026)
    assert rerun != written, "re-running the simulation must invalidate the cache"

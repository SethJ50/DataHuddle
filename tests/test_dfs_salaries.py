"""Tests for reading the sites' salary exports and storing them.

The two files describe the same slate and agree on almost nothing. What can go
wrong is mostly quiet:

1. NEITHER FILE CARRIES A PLAYER ID this app understands, so every row is matched
   by name. Exact matching resolves about 55% of a slate; the normalising below
   takes it past 97%.
2. DRAFTKINGS DOES NOT SAY WHO A PLAYER FACES -- only the fixture -- so the
   opponent has to be worked out, and getting it backwards gives every player his
   own team as an opponent.
3. THE STORAGE KEY MUST BE THE SITE'S OWN ID. Team defences and unmatched players
   arrive with no `canonical_id`, so keying on that would collapse them into one.
"""

import pandas as pd
import pytest

from adapters.dfs_salary_adapter import (
    COLUMNS, build_name_lookup, normalise_name, read_draftkings, read_fanduel,
)
from repositories.dfs_salary_repo import KEY_FIELDS, DfsSalaryRepo


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def test_a_generational_suffix_is_ignored():
    # The single biggest cause of a name failing to match.
    assert normalise_name("James Cook III") == normalise_name("James Cook")
    assert normalise_name("Travis Etienne Jr.") == normalise_name("Travis Etienne")
    assert normalise_name("Aaron Jones Sr.") == normalise_name("Aaron Jones")


def test_accents_and_punctuation_are_ignored():
    assert normalise_name("Ja'Marr Chase") == normalise_name("JaMarr Chase")
    assert normalise_name("Amon-Ra St. Brown") == normalise_name("Amon Ra St Brown")


def test_a_v_in_a_name_is_not_mistaken_for_a_suffix():
    # "V" is a generational suffix, but only standing alone.
    assert "vance" in normalise_name("Vance McDonald")


def test_the_lookup_covers_every_position_not_only_the_skill_ones():
    # The sites list fullbacks, tackles and long snappers under RB, TE and WR
    # because they are eligible receivers. Indexing only the skill positions
    # leaves about thirty rows a slate unmatched for no reason.
    players = pd.DataFrame({
        "display_name": ["A Back", "A Fullback", "A Tackle"],
        "position": ["RB", "FB", "OT"],
        "gsis_id": ["00-1", "00-2", "00-3"],
    })
    lookup = build_name_lookup(players)
    assert lookup[normalise_name("A Fullback")] == "00-2"
    assert lookup[normalise_name("A Tackle")] == "00-3"


def test_a_shared_name_resolves_to_the_skill_player():
    # There have been several Mike Williamses. A salary file listing one almost
    # certainly means the receiver, not the defensive tackle.
    players = pd.DataFrame({
        "display_name": ["Mike Williams", "Mike Williams"],
        "position": ["DT", "WR"],
        "gsis_id": ["00-DT", "00-WR"],
    })
    assert build_name_lookup(players)[normalise_name("Mike Williams")] == "00-WR"


# ---------------------------------------------------------------------------
# Reading the files
# ---------------------------------------------------------------------------


@pytest.fixture
def fanduel_file(tmp_path):
    path = tmp_path / "FDSalaries.csv"
    path.write_text(
        "Id,Position,First Name,Nickname,Last Name,FPPG,Played,Salary,Game,"
        "Team,Opponent,Injury Indicator,Injury Details,Tier,,,Roster Position\n"
        "1-1,RB,Jahmyr,Jahmyr Gibbs,Gibbs,20.0,17,9100,NO@DET,DET,NO,Q,Back,,,,RB/FLEX\n"
        "1-2,D,,Detroit Lions,,8.0,17,3900,NO@DET,DET,NO,,,,,,DEF\n"
    )
    return path


@pytest.fixture
def draftkings_file(tmp_path):
    path = tmp_path / "DKSalaries.csv"
    path.write_text(
        "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,"
        "TeamAbbrev,AvgPointsPerGame,Status\n"
        "RB,Jahmyr Gibbs (99),Jahmyr Gibbs,99,RB/FLEX,8000,"
        "NO@DET 09/13/2026 01:00PM ET,DET,22.3,Q\n"
        "WR,Chris Olave (98),Chris Olave,98,WR/FLEX,6000,"
        "NO@DET 09/13/2026 01:00PM ET,NO,14.1,\n"
        "DST,Lions (97),Lions,97,DST,3600,"
        "NO@DET 09/13/2026 01:00PM ET,DET,7.5,\n"
    )
    return path


def test_both_files_come_out_the_same_shape(fanduel_file, draftkings_file):
    fanduel = read_fanduel(fanduel_file, 2026, 1)
    draftkings = read_draftkings(draftkings_file, 2026, 1)
    assert list(fanduel.columns) == COLUMNS
    assert list(draftkings.columns) == COLUMNS


def test_fanduel_defences_are_renamed_to_the_app_s_word(fanduel_file):
    # FanDuel writes "D"; everything else in this app says "DST".
    frame = read_fanduel(fanduel_file, 2026, 1)
    assert set(frame["position"]) == {"RB", "DST"}


def test_the_draftkings_opponent_is_worked_out_from_the_fixture(draftkings_file):
    # DraftKings gives "NO@DET" and the player's own team, but never who he
    # faces. Backwards, every player would face himself.
    frame = read_draftkings(draftkings_file, 2026, 1).set_index("name")
    assert frame.loc["Jahmyr Gibbs", "opponent"] == "NO"    # DET player
    assert frame.loc["Chris Olave", "opponent"] == "DET"    # NO player


def test_the_kickoff_time_is_stripped_out_of_the_fixture(draftkings_file):
    frame = read_draftkings(draftkings_file, 2026, 1)
    assert set(frame["game"]) == {"NO@DET"}


def test_names_are_matched_to_player_ids(fanduel_file):
    lookup = {normalise_name("Jahmyr Gibbs"): "00-0039139"}
    frame = read_fanduel(fanduel_file, 2026, 1, lookup).set_index("name")
    assert frame.loc["Jahmyr Gibbs", "canonical_id"] == "00-0039139"


def test_a_defence_never_gets_a_player_id(draftkings_file):
    # A defence is a team, not a person. Even if some player happened to be
    # called "Lions", the row must not pick up his id.
    lookup = {normalise_name("Lions"): "00-WRONG"}
    frame = read_draftkings(draftkings_file, 2026, 1, lookup)
    assert frame.loc[frame["position"] == "DST", "canonical_id"].isna().all()


def test_an_unmatched_player_keeps_his_salary(fanduel_file):
    # A blank id is a normal outcome, not a reason to drop the row -- he is
    # still rosterable and still costs what he costs.
    frame = read_fanduel(fanduel_file, 2026, 1, {}).set_index("name")
    assert pd.isna(frame.loc["Jahmyr Gibbs", "canonical_id"])
    assert frame.loc["Jahmyr Gibbs", "salary"] == 9100


def test_the_slate_is_tagged_with_its_week(fanduel_file):
    frame = read_fanduel(fanduel_file, 2026, 3)
    assert set(frame["season"]) == {2026}
    assert set(frame["week"]) == {3}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class FakeDocuments:
    """Stands in for the database, holding rows in a list."""

    def __init__(self):
        self.rows = []
        self.indexes = []

    def ensure_index(self, collection, fields, unique=False, name=None):
        self.indexes.append((collection, tuple(fields), unique))

    def bulk_upsert(self, collection, docs, key_fields):
        for doc in docs:
            key = tuple(doc.get(field) for field in key_fields)
            self.rows = [r for r in self.rows
                         if tuple(r.get(f) for f in key_fields) != key]
            self.rows.append(doc)

    def find_all(self, collection, filter=None):
        filter = filter or {}
        return [r for r in self.rows
                if all(r.get(k) == v for k, v in filter.items())]


@pytest.fixture
def repo():
    return DfsSalaryRepo(documents=FakeDocuments())


def slate_frame(**overrides):
    row = {"site": "FanDuel", "season": 2026, "week": 1, "site_player_id": "1-1",
           "name": "A Player", "canonical_id": "00-1", "position": "RB",
           "roster_positions": "RB/FLEX", "salary": 9100, "team": "DET",
           "opponent": "NO", "game": "NO@DET", "site_projection": 20.0,
           "injury_status": None}
    row.update(overrides)
    return pd.DataFrame([row])


def test_a_slate_can_be_read_back(repo):
    repo.save_slate(slate_frame())
    assert len(repo.slate(2026, 1)) == 1


def test_reloading_a_week_replaces_it_rather_than_duplicating(repo):
    repo.save_slate(slate_frame(salary=9100))
    repo.save_slate(slate_frame(salary=9400))       # the price moved

    stored = repo.slate(2026, 1)
    assert len(stored) == 1
    assert stored["salary"].iloc[0] == 9400


def test_loading_a_week_leaves_other_weeks_alone(repo):
    # The whole reason for keeping history rather than overwriting.
    repo.save_slate(slate_frame(week=1, salary=9100))
    repo.save_slate(slate_frame(week=2, salary=8800))

    assert repo.slate(2026, 1)["salary"].iloc[0] == 9100
    assert repo.slate(2026, 2)["salary"].iloc[0] == 8800


def test_the_two_sites_do_not_overwrite_each_other(repo):
    repo.save_slate(slate_frame(site="FanDuel", salary=9100))
    repo.save_slate(slate_frame(site="DraftKings", site_player_id="99",
                                salary=8000))

    assert len(repo.slate(2026, 1)) == 2
    assert repo.slate(2026, 1, site="DraftKings")["salary"].iloc[0] == 8000


def test_rows_are_keyed_by_the_site_s_own_id_not_this_app_s():
    # Defences and unmatched players both arrive without a `canonical_id`.
    # Keying on that would collapse every one of them into a single row.
    assert "site_player_id" in KEY_FIELDS
    assert "canonical_id" not in KEY_FIELDS


def test_two_players_with_no_id_are_stored_separately(repo):
    repo.save_slate(slate_frame(site_player_id="1-1", canonical_id=None,
                                name="Lions"))
    repo.save_slate(slate_frame(site_player_id="1-2", canonical_id=None,
                                name="Bears"))
    assert len(repo.slate(2026, 1)) == 2


def test_an_empty_slate_writes_nothing(repo):
    assert repo.save_slate(pd.DataFrame()) == 0


def test_a_week_with_nothing_stored_returns_the_columns_anyway(repo):
    empty = repo.slate(2026, 17)
    assert empty.empty
    assert "salary" in empty.columns


def test_the_loaded_weeks_can_be_listed(repo):
    repo.save_slate(slate_frame(week=1))
    repo.save_slate(slate_frame(week=2, site_player_id="1-2"))

    slates = repo.available_slates()
    assert list(slates["week"]) == [2, 1]           # newest first
    assert set(slates["players"]) == {1}


def test_the_unique_index_covers_the_whole_key(repo):
    # Without it, a loader bug could store the same player twice for one slate
    # and every count built on the table would be wrong.
    repo.ensure_indexes()
    unique = [fields for _, fields, is_unique in repo._documents.indexes
              if is_unique]
    assert tuple(KEY_FIELDS) in unique

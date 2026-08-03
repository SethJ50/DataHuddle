"""Tests for cross-source player name normalization.

These lock in the rules that let one source's "James Cook III" match another's
"James Cook". Before this existed, 10 UDK-ranked players -- including an RB5 --
were silently dropped from the app's entire player universe, because
RosterService.roster() discards anything without a canonical_id.

Offline: normalize_name is pure string logic and needs no database.
"""

from repositories.player_directory import normalize_name


def test_strips_generational_suffixes():
    # The single biggest cause of cross-source mismatches.
    assert normalize_name("James Cook III") == "james cook"
    assert normalize_name("Michael Pittman Jr.") == "michael pittman"
    assert normalize_name("Kyle Pitts Sr.") == "kyle pitts"
    assert normalize_name("Odell Beckham Jr") == "odell beckham"


def test_strips_accents():
    # "Eddy Piñeiro" vs "Eddy Pineiro" -- same player, different bytes.
    assert normalize_name("Eddy Piñeiro") == "eddy pineiro"


def test_strips_punctuation():
    assert normalize_name("Tre' Harris") == "tre harris"
    assert normalize_name("A.J. Brown") == "a j brown"
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"


def test_bare_v_is_not_treated_as_a_suffix():
    # "V" is excluded from the suffix list on purpose: a lone letter is far more
    # likely to be part of a real name than a generational marker, and a wrong
    # strip is worse than a missed one.
    assert normalize_name("Player V") == "player v"


def test_does_not_do_fuzzy_matching():
    # Only known-meaningless variation is removed. Nicknames and shortened first
    # names must NOT collapse together -- they may be different people, and the
    # manual player_id_map exists for exactly those cases.
    assert normalize_name("Mike Williams") != normalize_name("Michael Williams")
    assert normalize_name("Hollywood Brown") != normalize_name("Marquise Brown")


def test_is_idempotent():
    # Normalizing an already-normalized name must not change it, since both sides
    # of a comparison get normalized.
    once = normalize_name("Amon-Ra St. Brown Jr.")
    assert normalize_name(once) == once

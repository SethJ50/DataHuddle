"""Central registry of MongoDB collection names and shared domain enums.

Every repository/adapter should import collection names from here instead of
using bare string literals, so a rename only needs to happen in one place.
"""

from enum import Enum


class Collections:
    """The name of every MongoDB collection the app reads or writes.

    Used as a namespace rather than something to create an instance of: write
    `Collections.DRAFTS`, never `Collections()`. Keeping the names here means a
    collection rename is a one-line change instead of a search across the
    codebase.

    Note that FFB_QB_PROJECTIONS and FFB_FLEX_PROJECTIONS are templates rather
    than finished names — they contain `{analyst}` and must be formatted with an
    analyst's name before use.
    """

    ESPN_PROJECTIONS = "espn_projections"
    SLEEPER_PROJECTIONS = "sleeper_projections"
    YAHOO_DRAFTANALYSIS = "yahoo_draftanalysis"
    # Fantasy Footballers projections, one pair of files per analyst. The
    # unsuffixed collections these replaced held a stale snapshot of Andy's
    # numbers and have been dropped.
    FFB_QB_PROJECTIONS = "ffb_qb_projections_{analyst}"
    FFB_FLEX_PROJECTIONS = "ffb_flex_projections_{analyst}"
    UDK_QB_RANKINGS = "udk_qb_rankings_ppr"
    UDK_RB_RANKINGS = "udk_rb_rankings_ppr"
    UDK_WR_RANKINGS = "udk_wr_rankings_ppr"
    UDK_TE_RANKINGS = "udk_te_rankings_ppr"
    # K and DST are RANKINGS, not projections -- consensus rank plus each
    # analyst's rank, no points. Stored and queryable, but deliberately outside
    # RosterService's player universe (see FfbKdstAdapter).
    UDK_K_RANKINGS = "udk_k_rankings_ppr"
    UDK_DST_RANKINGS = "udk_dst_rankings_ppr"
    DRAFTS = "drafts"
    PLAYER_MARKINGS = "player_markings"
    TEAM_NOTES = "team_notes"
    FFC_ADP = "ffc_adp"
    ADP_SNAPSHOTS = "adp_snapshots"

    PLAYER_ID_MAP = "player_id_map"
    PLAYER_NOTES = "player_notes"
    PLAYER_CATEGORIES = "player_categories"
    ADP_ESPN = "adp_espn"
    ADP_YAHOO = "adp_yahoo"
    ADP_SLEEPER = "adp_sleeper"
    DRAFT_PLANS = "draft_plans"
    DRAFT_SESSIONS = "draft_sessions"

MARKING_CATEGORIES = [
    "Safe",
    "Upside",
    "Love",
    "Like",
    "Uncertain Backfield",
    "New Top 12 Receiver",
]

class Position(str, Enum):
    """The positions this app recognizes, in its own canonical spelling.

    Every external source spells some of these differently — "PK" for kicker,
    "DEF" or "D" for defense — so anything arriving from outside should go
    through `canonical_position` below before being turned into one of these.

    Inheriting from `str` as well as `Enum` means each value behaves like its own
    text, so `Position.RB == "RB"` is True.
    """

    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    K = "K"
    DST = "DST"

POSITION_ALIASES = {
    "DEF": "DST",     # Yahoo, and Fantasy Football Calculator
    "D": "DST",       # UDK's K/DST rankings export
    "PK": "K",        # Fantasy Football Calculator
}

def canonical_position(raw: str) -> str:
    """Translate one source's spelling of a position into the app's own.

    Different providers name the same positions differently, and letting those
    variants spread through the codebase would mean every comparison had to know
    about all of them. Everything from outside passes through here first.

    Steps:
        1. Trim surrounding whitespace and uppercase the token, so " wr " and
           "WR" are treated the same.
        2. Look that up in POSITION_ALIASES, falling back to the cleaned-up token
           itself when there is no alias.

    Args:
        raw: A position token from any external source, such as "PK", "def", or
            " WR ".

    Returns:
        str: The canonical spelling, such as "K", "DST", or "WR". An unknown
            token is passed through uppercased rather than dropped, so a new
            position from a source shows up visibly instead of disappearing.

    Raises:
        AttributeError: If `raw` is not a string. A missing value must be
            filtered out before calling.

    Note:
        Deliberately NOT returning a Position enum -- some callers (the FFC
        adapter) want a plain string column in a DataFrame. Use Position(...) on
        the result if you need the enum.
    """
    return POSITION_ALIASES.get(raw.strip().upper(), raw.strip().upper())

def parse_positions(raw: str) -> list[Position]:
    """Split a multi-position eligibility string into a list of Positions.

    Some players are eligible at more than one position, and sources write that
    as a single comma-separated string such as Yahoo's "RB,TE". This turns it
    into something the code can actually check against.

    Steps:
        1. Split the string on commas.
        2. Run each token through `canonical_position` above to normalize its
           spelling, then turn it into a Position.
        3. Skip any token that is not a position this app recognizes, rather than
           raising, since eligibility strings vary by source.

    Args:
        raw: A comma-separated eligibility string, such as "RB,TE". A single
            position with no comma works fine too.

    Returns:
        list: The recognized positions, in the order they appeared. Can be empty
            if none of the tokens were recognized, so callers should not assume
            at least one.
    """
    positions = []
    for token in raw.split(","):
        try:
            positions.append(Position(canonical_position(token)))
        except ValueError:
            continue
    return positions

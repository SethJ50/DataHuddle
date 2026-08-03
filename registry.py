"""Central registry of MongoDB collection names and shared domain enums.

Every repository/adapter should import collection names from here instead of
using bare string literals, so a rename only needs to happen in one place.
"""

from enum import Enum


class Collections:
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

MARKING_CATEGORIES = [
    "Safe",
    "Upside",
    "Love",
    "Like",
    "Uncertain Backfield",
    "New Top 12 Receiver",
]

class Position(str, Enum):
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
    """
    Purpose: Translate one source's position spelling into ours.

    Parameters:
        raw (str): A position token from any external source, e.g. "PK", "def", " WR ".

    Returns:
        str: The canonical spelling ("K", "DST", "WR"). Unknown tokens are passed
            through uppercased rather than dropped, so a new position from a
            source shows up visibly instead of disappearing.

    Notes:
        Deliberately NOT returning a Position enum -- some callers (the FFC
        adapter) want a plain string column in a DataFrame. Use Position(...) on
        the result if you need the enum.
    """
    return POSITION_ALIASES.get(raw.strip().upper(), raw.strip().upper())

def parse_positions(raw: str) -> list[Position]:
    """Split a dual-eligibility position string (Yahoo's "RB,TE") into Positions.

    Unrecognized tokens are skipped rather than raising, since eligibility
    strings vary by source. Aliasing is handled by canonical_position() so
    every source's quirks live in one table.
    """
    positions = []
    for token in raw.split(","):
        try:
            positions.append(Position(canonical_position(token)))
        except ValueError:
            continue
    return positions

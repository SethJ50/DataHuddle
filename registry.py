"""Central registry of MongoDB collection names and shared domain enums.

Every repository/adapter should import collection names from here instead of
using bare string literals, so a rename only needs to happen in one place.
"""

from enum import Enum


class Collections:
    ESPN_PROJECTIONS = "espn_projections"
    SLEEPER_PROJECTIONS = "sleeper_projections"
    YAHOO_DRAFTANALYSIS = "yahoo_draftanalysis"
    FFB_QB_PROJECTIONS = "ffb_qb_projections"
    FFB_FLEX_PROJECTIONS = "ffb_flex_projections"
    UDK_QB_RANKINGS = "udk_qb_rankings_ppr"
    UDK_RB_RANKINGS = "udk_rb_rankings_ppr"
    UDK_WR_RANKINGS = "udk_wr_rankings_ppr"
    UDK_TE_RANKINGS = "udk_te_rankings_ppr"
    DRAFTS = "drafts"
    PLAYER_MARKINGS = "player_markings"

    # Planned per PLANNING.md — collections don't exist yet, constants reserved
    # so future repositories/adapters have one place to reference them from.
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


def parse_positions(raw: str) -> list[Position]:
    """Split a dual-eligibility position string (Yahoo's "RB,TE") into Positions.

    Unrecognized tokens are skipped rather than raising, since eligibility
    strings vary by source (e.g. Yahoo uses "DEF" instead of "DST").
    """
    positions = []
    for token in raw.split(","):
        token = token.strip().upper()
        if token == "DEF":
            token = "DST"
        try:
            positions.append(Position(token))
        except ValueError:
            continue
    return positions

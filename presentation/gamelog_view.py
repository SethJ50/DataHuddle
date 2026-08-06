"""Position-aware column selection/sorting for a player's game log table."""

import pandas as pd


class GameLogView:
    """Chooses which game-log columns to show, based on the player's position.

    A quarterback's game log and a receiver's have almost nothing in common: one
    wants passing stats, the other receiving. Showing every column for everyone
    would bury the interesting numbers among empty ones, so the column list is
    assembled per position from the groups below.

    Everything here is a classmethod, so call `GameLogView.shape(...)` directly
    rather than creating one.
    """

    # Columns shown for every position: when the game was and against whom.
    BASIC_COLS = ["week", "season", "team", "opponent_team"]

    PASSING_COLS = [
        "attempts", "completions", "passing_yards", "passing_tds",
        "passing_interceptions", "passing_2pt_conversions", "sack_fumbles_lost",
    ]

    RUSHING_COLS = [
        "carries", "rushing_yards", "rushing_tds",
        "rushing_fumbles_lost", "rushing_2pt_conversions",
    ]

    RECEIVING_COLS = [
        "receptions", "targets", "receiving_yards", "receiving_tds",
        "receiving_fumbles_lost", "receiving_2pt_conversions",
    ]

    @classmethod
    def shape(cls, gamelog_data, position=None):
        """Pick the right columns for a player's position and sort by most recent.

        Called by the player profile page to turn a raw stats table into the game
        log shown on screen.

        Steps:
            1. Choose the column list from the player's position. Quarterbacks
               get passing then rushing; running backs get rushing then
               receiving; any other known position gets receiving then rushing.
            2. If the position is unknown, fall back to the basic columns alone
               rather than guessing at stat groups.
            3. Return an empty table if the player has no recorded games, which
               is normal for a rookie.
            4. Keep only the chosen columns that the data actually has, so a
               column missing from this season's data is skipped instead of
               raising.
            5. Sort by season then week, both descending, so the most recent game
               is at the top.

        Args:
            gamelog_data: One row per game for a single player, from
                `PlayerDirectory.get_gamelog`.
            position: The player's position, such as "QB" or "WR". None or an
                unrecognized value falls back to the basic columns.

        Returns:
            pd.DataFrame: The selected columns, most recent game first. Empty
                when the player has no recorded games.
        """
        if position == "QB":
            display_cols = cls.BASIC_COLS + cls.PASSING_COLS + cls.RUSHING_COLS
        elif position == "RB":
            display_cols = cls.BASIC_COLS + cls.RUSHING_COLS + cls.RECEIVING_COLS
        elif position is not None:
            display_cols = cls.BASIC_COLS + cls.RECEIVING_COLS + cls.RUSHING_COLS
        else:
            display_cols = cls.BASIC_COLS

        if gamelog_data.empty:
            return pd.DataFrame()

        available_cols = [col for col in display_cols if col in gamelog_data.columns]

        return gamelog_data[available_cols].sort_values(
            by=["season", "week"], ascending=[False, False]
        )
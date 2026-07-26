"""Position-aware column selection/sorting for a player's game log table."""

import pandas as pd


class GameLogView:
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
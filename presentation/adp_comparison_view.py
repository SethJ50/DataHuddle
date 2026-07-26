"""
Shapes AdpComparisonService's table output for on-screen display.
"""

import pandas as pd

class AdpComparisonView:

    COLUMN_LABELS = {
        "player": "Player",
        "position": "Position",
        "espn_adp": "ESPN ADP",
        "yahoo_adp": "Yahoo ADP",
        "sleeper_adp": "Sleeper ADP"
    }

    @classmethod
    def shape(cls, df: pd.DataFrame, position: str = "All"):
        """
        Purpose: Filters by position and reshapes the comparison data into
            display-ready columns.

        Parameters:
            df (pd.DataFrame): AdpComparisonService.compare()'s output.
            position (str): a Position value (e.g. "WR") to filter to, or
                "All" (default) to show every position.

        Returns:
            pd.DataFrame with columns [player, position, espn_adp, yahoo_adp,
            sleeper_adp], renamed to their display labels, ready to hand to
            a table renderer (shiny.render.DataGrid or Streamlit's
            st.dataframe).

        Notes:
            Headshot images were tried in this table (both as Shiny
            ui.img() tags, which hit a client-side serialization error, and
            later as Streamlit's ImageColumn, which worked but wasn't kept)
            -- this view intentionally has no headshot column now.
        """

        if df.empty:
            return pd.DataFrame(columns=list(cls.COLUMN_LABELS.values()))

        if position != "All":
            df = df[df["position"] == position]

        display = pd.DataFrame({
            "player": df["display_name"],
            "position": df["position"],
            "espn_adp": df["espn_adp"],
            "yahoo_adp": df["yahoo_adp"],
            "sleeper_adp": df["sleeper_adp"],
        })

        return display.rename(columns=cls.COLUMN_LABELS)
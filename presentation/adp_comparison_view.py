"""
Shapes AdpComparisonService's table output for on-screen display.
"""

import pandas as pd

class AdpComparisonView:
    """Reshapes the ADP comparison table into something ready to display.

    A "view" in this app is the last step before rendering: it picks which
    columns appear, in what order, and under what headings. Keeping that apart
    from the service means the service can stay focused on the numbers.

    Everything here is a classmethod, so there is no need to create one of
    these — call `AdpComparisonView.shape(...)` directly.
    """

    # Real column name -> the heading shown to the reader.
    COLUMN_LABELS = {
        "player": "Player",
        "position": "Position",
        "espn_adp": "ESPN ADP",
        "yahoo_adp": "Yahoo ADP",
        "sleeper_adp": "Sleeper ADP"
    }

    @classmethod
    def shape(cls, df: pd.DataFrame, position: str = "All"):
        """Filter by position and reshape the comparison data for display.

        The single step between the comparison service's output and the table on
        screen.

        Steps:
            1. If the incoming table has no rows, return an empty table that
               already carries the display headings, so the page renders an empty
               grid rather than failing on missing columns.
            2. Unless "All" was asked for, keep only rows at the chosen position.
            3. Build a new table with just the columns worth showing, dropping
               the internal ones such as `canonical_id` and `headshot_url`.
            4. Rename the headers to their display labels.

        Args:
            df: The output of `AdpComparisonService.compare()`.
            position: A position such as "WR" to filter to, or "All", the
                default, to show every position.

        Returns:
            pd.DataFrame: Columns Player, Position, ESPN ADP, Yahoo ADP, and
                Sleeper ADP, ready to hand to a table renderer such as
                Streamlit's `st.dataframe`. A platform that does not rank a
                player leaves a blank cell.

        Note:
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
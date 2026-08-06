"""Reusable Pandas Styler helpers for Streamlit st.dataframe tables.

These produce cell-level styles (colors) that Streamlit renders behind its
column widgets. Rule of thumb: use column_config for value formatting, and use
these helpers for coloring (conditional formatting).
"""

import pandas as pd


def highlight_true(color_by_col: dict):
    """Build a styling function that colors in every checked checkbox cell.

    Used to make the marking columns on the draft board readable at a glance: a
    checked box becomes a colored block rather than a tick you have to hunt for.

    This does not style anything itself — it RETURNS a function that does, which
    is what pandas' `.apply` expects.

    Steps:
        1. Define an inner function that pandas will call with the columns being
           styled.
        2. Return that inner function without calling it. It remembers
           `color_by_col` from this call, so each use can have its own colors.

    Args:
        color_by_col: Maps a column name to the CSS color to use when a cell in
            that column is checked. Columns absent from this map are left
            unstyled.

    Returns:
        A function suitable for `df.style.apply(fn, subset=..., axis=None)`. It
            receives the sub-table being styled and returns a same-shaped table
            of CSS strings.
    """
    def _style(sub_df: pd.DataFrame) -> pd.DataFrame:
        """Produce the CSS for each cell of the table being styled.

        Called by pandas, not by app code. It receives whatever columns were
        selected and must return a table of exactly the same shape holding CSS
        strings.

        Steps:
            1. Build a table of empty strings matching the incoming shape, which
               means "no styling anywhere" as a starting point.
            2. For each column, look up its color and skip it if it has none.
            3. Fill that column with a background-color rule for the checked
               cells and an empty string for the rest.

        Args:
            sub_df: The columns pandas selected for styling.

        Returns:
            pd.DataFrame: The same shape as the input, holding a CSS string per
                cell — "background-color: ..." for checked cells and an empty
                string otherwise.
        """
        # Start with no styling, matching the shape of the incoming columns.
        styles = pd.DataFrame("", index=sub_df.index, columns=sub_df.columns)
        for col in sub_df.columns:
            color = color_by_col.get(col)
            if color:
                # Truthy (checked) -> paint the background; else leave blank.
                styles[col] = sub_df[col].map(
                    lambda v: f"background-color: {color}" if v else ""
                )
        return styles

    return _style

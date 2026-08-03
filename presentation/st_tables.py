"""Reusable Pandas Styler helpers for Streamlit st.dataframe tables.

These produce cell-level styles (colors) that Streamlit renders behind its
column widgets. Rule of thumb: use column_config for value formatting, and use
these helpers for coloring (conditional formatting).
"""

import pandas as pd


def highlight_true(color_by_col: dict):
    """
    Purpose:
        Build a Pandas Styler function that paints a cell's background whenever
        its value is truthy (e.g. a checked boolean column). Handy for
        conditional formatting of checkbox / flag columns in st.dataframe.

    Parameters:
        color_by_col (dict): Maps a column name to the CSS color used when a
            cell in that column is truthy. Columns absent from the map (or whose
            cell value is falsy) are left unstyled.

    Returns:
        A function suitable for `df.style.apply(fn, subset=..., axis=None)`. That
        function receives the styled sub-DataFrame and returns a same-shaped
        DataFrame of CSS strings ("background-color: ..." for truthy cells, ""
        otherwise).

    Notes:
        Returning a closure lets each call bind its own color map, so the same
        helper works for any page's set of columns and colors.
    """
    def _style(sub_df: pd.DataFrame) -> pd.DataFrame:
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

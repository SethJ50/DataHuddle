"""Styles a DataFrame as a Pandas Styler for Shiny table rendering."""


def style_table(df, col_mapping: dict = None):
    """Turn a plain DataFrame into a styled HTML table with the app's look.

    A "Styler" is pandas' way of attaching formatting to a table without
    changing the data in it. This applies one shared set of colors, borders, and
    fonts so every table in the app looks the same.

    Steps:
        1. Return the table untouched if it has no rows, since there is nothing
           to style and the calls below would have no effect anyway.
        2. Start a Styler with the row-number index hidden, which is almost never
           meaningful to a reader.
        3. If a column-name mapping was supplied, rename the headers through it.
        4. Show missing values as blank cells rather than the literal text "NaN".
        5. Round every numeric column to whole numbers.
        6. Attach the CSS: full-width layout, a dark header row, thin separators
           between columns and rows, alternating row shading, and a hover
           highlight.

    Args:
        df: The table to style. Its data is never modified.
        col_mapping: Optional mapping from the real column name to the heading to
            display instead. Columns missing from it keep their own name.

    Returns:
        pd.io.formats.style.Styler: The styled table, ready to render. Note that
            an EMPTY input comes back as the DataFrame itself rather than a
            Styler, so callers that call Styler methods on the result should
            check for rows first.
    """
    if df.empty:
        return df

    styler = df.style.hide(axis="index")

    if col_mapping is not None:
        styler.format_index(col_mapping.get, axis="columns")

    # Show missing values as blank cells instead of the literal text "NaN".
    styler = styler.format(na_rep="")

    numeric_cols = df.select_dtypes(include=['number']).columns
    styler = styler.format(subset=numeric_cols, precision=0, na_rep="")

    styler = styler.set_table_styles([
        {
            'selector': 'table',
            'props': [('width', '100%'), ('border-collapse', 'collapse'), ('font-family', 'sans-serif')]
        },

        # Table Header
        {
            'selector': 'th',
            'props': [
                ('background-color', '#1a1a1a'),
                ('color', 'white'),
                ('padding', '6px 8px'),
                ('text-align', 'center'),
                ('font-weight', 'bold'),
                ('font-size', '12px')
            ]
        },

        # Lines Between Header Names
        {
            'selector': 'th:not(:first-child)',
            'props': [('border-left', '1px solid #444444')]
        },

        # Table Rows
        {
            'selector': 'td',
            'props': [
                ('padding', '1px 1px'),
                ('text-align', 'center'),
                ('border-bottom', '1px solid #e0e0e0'),
                ('font-size', '12px')
            ]
        },

        # Lines Between Columns
        {
            'selector': 'td:not(:first-child)',
            'props': [('border-left', '1px solid #e0e0e0')]
        },

        # Alternate Background Color
        {
            'selector': 'tr:nth-child(even)',
            'props': [('background-color', '#f9f9f9')]
        },

        # Change Row Background on Hover
        {
            'selector': 'tr:hover',
            'props': [('background-color', '#f1f1f1')]
        }
    ])

    return styler
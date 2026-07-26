"""Styles a DataFrame as a Pandas Styler for Shiny table rendering."""


def style_table(df, col_mapping: dict = None):
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
"""Reusable Pandas Styler helpers for Streamlit st.dataframe tables.

These produce cell-level styles (colors) that Streamlit renders behind its
column widgets. Rule of thumb: use column_config for value formatting, and use
these helpers for coloring (conditional formatting).
"""

import pandas as pd


def shade_columns(color_by_col: dict):
    """Build a styling function that washes whole columns in a background colour.

    Used to band a wide table into groups: a table forty columns across is much
    easier to read when you can see where the passing statistics stop and the
    rushing ones start. Streamlit has no way to draw a heading ABOVE a set of
    columns, so the grouping is shown by shading alternate groups instead.

    Like `highlight_true` below, this does not style anything itself — it RETURNS
    a function that does, which is what pandas' `.apply` expects.

    Steps:
        1. Define an inner function that pandas will call with the columns being
           styled.
        2. Return that inner function without calling it. It remembers
           `color_by_col` from this call.

    Args:
        color_by_col: Maps a column name to the CSS colour to wash it with.
            Columns absent from the map are left alone, which is how the
            un-shaded half of the banding is expressed.

    Returns:
        A function suitable for `df.style.apply(fn, axis=None)`. It receives the
            table being styled and returns a same-shaped table of CSS strings.

    Note:
        Use a TRANSLUCENT colour. The surface underneath sets the lightness, so
        one value reads correctly on both the light and the dark theme and there
        is nothing to switch. A solid colour would have to be chosen twice.
    """
    def _style(sub_df: pd.DataFrame) -> pd.DataFrame:
        """Produce the CSS for each cell of the table being styled.

        Called by pandas, not by app code.

        Steps:
            1. Build a table of empty strings matching the incoming shape, which
               means "no styling anywhere" as a starting point.
            2. For each column with a colour, fill the whole column with a
               background rule.

        Args:
            sub_df: The table pandas selected for styling.

        Returns:
            pd.DataFrame: The same shape as the input, holding a CSS string per
                cell.
        """
        styles = pd.DataFrame("", index=sub_df.index, columns=sub_df.columns)
        for col in sub_df.columns:
            color = color_by_col.get(col)
            if color:
                styles[col] = f"background-color: {color}"
        return styles

    return _style


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

def color_scale(direction_by_column, blue="42, 120, 214", red="205, 66, 54",
                max_alpha=0.45):
    """Build a styling function that washes cells blue for good, red for bad.

    The spreadsheet idea: instead of reading forty numbers, you see where the
    good ones are. Blue is better, red is worse, and the middle of the column is
    left alone — so the eye lands on the extremes, which is the only part worth
    reading first.

    Steps:
        1. Define an inner function that pandas will call with the whole table.
        2. For each column that declared a direction, rank its values from 0 to
           1 — a PERCENTILE, so the middle player is always the middle colour
           however lopsided the column is.
        3. Flip that rank for a column where a smaller number is better.
        4. Turn each rank into a translucent wash: full blue at the top, nothing
           at the middle, full red at the bottom.

    Args:
        direction_by_column: Maps a column name to `"higher"` when a bigger
            number is better or `"lower"` when a smaller one is. Columns absent
            from the map are left unpainted, which is what makes this opt-in.
        blue: The "good" hue as "r, g, b".
        red: The "bad" hue as "r, g, b".
        max_alpha: How strong the wash gets at the very ends. Low enough that the
            number underneath stays readable.

    Returns:
        A function suitable for `df.style.apply(fn, axis=None)`.

    Note:
        PERCENTILE RANK, NOT THE RAW VALUE. One 45-point week in a column of
        teens would, on a linear scale, push everybody else to almost-white and
        colour a single cell. Ranking spreads the colour evenly, which is what
        makes it readable at a glance.

        TRANSLUCENT, so the app's own surface shows through and ONE palette works
        on both the light and the dark theme. "Middling" is therefore no wash at
        all rather than a painted white, which is also why a blank cell and an
        average one look alike -- see the note on the caller.
    """
    def _style(sub_df: pd.DataFrame) -> pd.DataFrame:
        """Produce the CSS for each cell. Called by pandas, not by app code."""
        styles = pd.DataFrame("", index=sub_df.index, columns=sub_df.columns)

        for column in sub_df.columns:
            direction = direction_by_column.get(column)
            if direction is None:
                continue

            values = pd.to_numeric(sub_df[column], errors="coerce")
            if values.notna().sum() < 2:
                continue          # one value is not a distribution

            # 0 = worst, 1 = best, 0.5 = the middle of the column.
            rank = values.rank(pct=True)
            if direction == "lower":
                rank = 1.0 - rank

            def wash(position):
                """Turn one rank into a colour, or nothing near the middle."""
                if pd.isna(position):
                    return ""
                strength = abs(position - 0.5) * 2 * max_alpha
                if strength < 0.02:
                    return ""
                hue = blue if position > 0.5 else red
                return f"background-color: rgba({hue}, {strength:.3f})"

            styles[column] = rank.map(wash)

        return styles

    return _style

def diverging_wash(position, blue="42, 120, 214", red="205, 66, 54",
                   max_alpha=0.45):
    """Turn a 0-1 position into a translucent blue or red, or into nothing.

    The shared colour rule behind both `color_scale` above and
    `shade_by_percentile` below, so a cell painted from a league percentile and
    one painted from its own column mean the same thing at the same intensity.

    Args:
        position: Where the value sits, 0 worst to 1 best. NaN gives no colour.
        blue: The "good" hue as "r, g, b".
        red: The "bad" hue as "r, g, b".
        max_alpha: How strong the wash gets at the very ends.

    Returns:
        str: A CSS declaration, or "" for the middle of the range and for
            missing values.
    """
    if position is None or pd.isna(position):
        return ""
    strength = abs(position - 0.5) * 2 * max_alpha
    if strength < 0.02:
        return ""
    hue = blue if position > 0.5 else red
    return f"background-color: rgba({hue}, {strength:.3f})"


def shade_by_percentile(column, percentiles, **wash):
    """Wash one column using a percentile supplied per row.

    Used where the ranking comes from OUTSIDE the table -- a player's standing in
    the league, rather than his standing among the rows on screen.

    Steps:
        1. Define an inner function pandas will call with the table.
        2. Paint the named column from the percentiles, converting each from
           0-100 to the 0-1 the wash expects.

    Args:
        column: Which column to paint.
        percentiles: A 0-100 value per row, lined up with the table's index.
        **wash: Passed through to `diverging_wash` above.

    Returns:
        A function suitable for `df.style.apply(fn, axis=None)`.
    """
    def _style(sub_df: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=sub_df.index, columns=sub_df.columns)
        if column in sub_df.columns:
            styles[column] = [diverging_wash(p / 100 if pd.notna(p) else p, **wash)
                              for p in percentiles]
        return styles

    return _style


def shade_cells(percentiles, **wash):
    """Wash every cell of a table from a same-shaped grid of percentiles.

    The cell-by-cell counterpart to `shade_by_percentile` above. A game log is
    one row per week, and what makes a number worth noticing is how good that
    week was compared with everybody else's -- which is a different percentile
    per cell, not per column.

    Steps:
        1. Define an inner function pandas will call with the table.
        2. Paint each column the grid has an entry for, converting each
           percentile from 0-100 into the 0-1 the wash expects.

    Args:
        percentiles: A frame with the SAME index and columns as the table being
            styled, holding 0-100 per cell. NaN leaves a cell unpainted, which is
            what an unranked stat and an unmatched week both produce.
        **wash: Passed through to `diverging_wash` above.

    Returns:
        A function suitable for `df.style.apply(fn, axis=None)`.

    Note:
        RANKED AGAINST THE LEAGUE, not against the rows on screen. Five rows are
        far too few to rank within, and "his best week of these five" is a much
        less useful thing to know than "a top-decile week for a receiver".
    """
    def _style(sub_df: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=sub_df.index, columns=sub_df.columns)
        for column in sub_df.columns:
            if column in percentiles.columns:
                styles[column] = [
                    diverging_wash(p / 100 if pd.notna(p) else p, **wash)
                    for p in percentiles[column]
                ]
        return styles

    return _style


def highlight_where(column, flags, color):
    """Wash one column's cells wherever a separately supplied flag is true.

    For marking a row by something that is NOT in the table -- a tag you saved
    against the player, say. The flag lives outside the data being drawn, so
    neither `highlight_true` nor `color_scale` above can reach it.

    Steps:
        1. Define an inner function pandas will call with the table.
        2. Paint the named column wherever the matching flag is true, and leave
           every other cell alone.

    Args:
        column: Which column to paint.
        flags: One True/False per row, lined up with the table's rows.
        color: The CSS colour to wash with. Use a translucent one, so the text
            stays readable and one value works on both themes.

    Returns:
        A function suitable for `df.style.apply(fn, axis=None)`.
    """
    def _style(sub_df: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=sub_df.index, columns=sub_df.columns)
        if column in sub_df.columns:
            styles[column] = [f"background-color: {color}" if flag else ""
                              for flag in flags]
        return styles

    return _style

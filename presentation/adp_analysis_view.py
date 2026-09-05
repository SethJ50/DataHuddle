"""Shapes and colours the ADP Analysis table for Streamlit.

Two jobs, kept apart on purpose because Streamlit wants them in different
places: choosing and renaming the columns (plain pandas), and colouring the
difference cells (a pandas Styler). Value FORMATTING is neither -- that belongs
to `column_config` on the page, per the app's table convention.
"""

import numpy as np
import pandas as pd

# Internal column -> the heading shown on screen. Deliberately terse: fifteen
# columns only fit across a screen if the headings stay narrow.
DISPLAY_COLUMNS = {
    "name":               "Player",
    "position":           "Pos",
    # Left to right, these follow the pipeline the simulation actually runs:
    # what the market did -> what the board says -> the blend of the two that
    # build_table receives -> the target it produced -> what the sim drafted.
    "plat_adp_adj":       "Plat ADP",
    "plat_adp_rank_adj":  "Plat Rk",
    "platform_rank_adj":  "Board Rk",
    "blend_adp_adj":      "Blend ADP",
    "blend_adp_rank_adj": "Blend Rk",
    "target_adp":         "Tgt ADP",
    "target_adp_rank":    "Tgt Rk",
    "sim_adp":            "Sim ADP",
    "sim_adp_rank":       "Sim Rk",
    "draft_rate":         "Drafted %",
    "ffb_rank_adj":       "FFB Rk",
    "diff_market_vs_board": "Mkt vs Board",
    "diff_sim_vs_board":    "Sim vs Board",
    "diff_sim_vs_target":   "Sim vs Tgt",
    "diff_market_vs_ffb":   "Mkt vs FFB",
    "diff_sim_vs_ffb":      "Sim vs FFB",
    "diff_board_vs_ffb":    "Board vs FFB",
}

# The six comparison columns, which are the point of the page and the only ones
# that get coloured. Every heading reads "A vs B" and holds A minus B.
DIFF_COLUMNS = ["Mkt vs Board", "Sim vs Board", "Sim vs Tgt",
                "Mkt vs FFB", "Sim vs FFB", "Board vs FFB"]

# Which columns are restated for the league's keepers, so the page can say so
# rather than leaving the reader to guess.
KEEPER_ADJUSTED = ["Plat ADP", "Plat Rk", "Board Rk", "Blend ADP", "Blend Rk",
                   "FFB Rk"]

POSITIVE_HUE = (34, 160, 90)     # green: goes LATER than rated -- value
NEGATIVE_HUE = (208, 66, 66)     # red:   goes EARLIER than rated -- a reach
MAX_ALPHA = 0.55


def shape(df: pd.DataFrame, position: str = "All", search: str = "") -> pd.DataFrame:
    """Filter the analysis table and rename its columns for display.

    The single step between the analysis service's output and the table on
    screen.

    Steps:
        1. Return an empty table that already carries the display headings if
           there is nothing to show, so the page renders an empty grid rather
           than failing on missing columns.
        2. Drop the kept players. Every rank and simulated column is blank for
           somebody nobody can draft, so those rows carry no information.
        3. Unless "All" was asked for, keep only rows at the chosen position.
        4. If a search term was typed, keep only players whose name contains it,
           ignoring capitalisation.
        5. Narrow to the columns worth showing and rename them.
        6. Sort by the simulation's own ADP so the earliest-drafted player comes
           first, putting anyone the simulation never drafts at the bottom.

    Args:
        df: The output of `AdpAnalysisService.build()`.
        position: A position such as "WR" to filter to, or "All" for everyone.
        search: Free text to match against the player's name. Empty shows all.

    Returns:
        pd.DataFrame: One row per surviving player with the display headings
            from DISPLAY_COLUMNS, in that order.
    """
    if df.empty:
        return pd.DataFrame(columns=list(DISPLAY_COLUMNS.values()))

    rows = df
    if "is_kept" in rows.columns:
        rows = rows[~rows["is_kept"].astype(bool)]

    if position != "All":
        rows = rows[rows["position"] == position]

    query = search.strip()
    if query:
        rows = rows[rows["name"].str.contains(query, case=False, na=False)]

    # na_position keeps players the simulation never drafted at the bottom,
    # where they belong, rather than at the top where NaN would sort them.
    rows = rows.sort_values("sim_adp", na_position="last")

    present = {k: v for k, v in DISPLAY_COLUMNS.items() if k in rows.columns}
    return rows[list(present)].rename(columns=present).reset_index(drop=True)


def diff_scale(column: pd.Series) -> float:
    """Work out how large a difference has to be to get full colour.

    Each difference column has its own natural spread -- a rank gap runs much
    wider than the calibration residual -- so sharing one scale would leave some
    columns almost colourless and others saturated everywhere. This gives each
    column its own.

    Steps:
        1. Drop missing values and take the size of each difference, ignoring
           whether it is positive or negative.
        2. Return a scale of 1.0 if nothing is left, which simply means no
           colour rather than a division by zero.
        3. Take the 90th percentile rather than the maximum, so one extreme
           player cannot wash out the colour for everybody else.
        4. Never return zero, which would make every cell fully saturated.

    Args:
        column: One difference column.

    Returns:
        float: The difference size that earns the strongest tint.
    """
    magnitudes = column.dropna().abs()
    if magnitudes.empty:
        return 1.0
    return float(max(np.percentile(magnitudes, 90), 1.0))


def tint(value, scale) -> str:
    """Turn one difference into a background colour.

    Steps:
        1. Return no colour for a missing value, so blank cells stay blank.
        2. Choose green for a positive difference and red for a negative one.
        3. Scale the opacity by how large the difference is against `scale`,
           capping it so nothing becomes unreadable.
        4. Render it as an `rgba(...)` string.

    Args:
        value: The difference for one player.
        scale: What counts as a large difference in this column, from
            `diff_scale` above.

    Returns:
        str: A CSS background declaration, or an empty string for no colour.

    Note:
        TRANSLUCENT on purpose. The surface underneath sets the lightness, so
        one value reads correctly on both the light and the dark theme and there
        is nothing to switch. A solid colour would have to be chosen twice.
    """
    if value is None or not np.isfinite(value):
        return ""

    red, green, blue = POSITIVE_HUE if value > 0 else NEGATIVE_HUE
    alpha = min(abs(value) / scale, 1.0) * MAX_ALPHA
    return f"background-color: rgba({red}, {green}, {blue}, {alpha:.3f})"


def style(display_df: pd.DataFrame):
    """Colour every difference column, each on its own scale.

    Green means the player goes LATER than that source rates him, which is where
    value hides. Red means he goes EARLIER, which is where you would have to
    reach.

    Steps:
        1. Return the table untouched if it has no rows; a Styler over an empty
           frame has nothing to colour and some callers check for rows anyway.
        2. Work out one scale per difference column with `diff_scale` above.
        3. Build a same-shaped table of CSS strings, filling only the difference
           columns and leaving every other cell blank.
        4. Hand that to pandas as a single whole-table styling function.

    Args:
        display_df: The output of `shape` above, using display headings.

    Returns:
        pd.io.formats.style.Styler: Ready for `st.dataframe`. An EMPTY input
            comes back as the DataFrame itself rather than a Styler.
    """
    if display_df.empty:
        return display_df

    columns = [c for c in DIFF_COLUMNS if c in display_df.columns]
    scales = {c: diff_scale(display_df[c]) for c in columns}

    def _paint(frame: pd.DataFrame) -> pd.DataFrame:
        """Produce the CSS for every cell. Called by pandas, not by app code."""
        css = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for column in columns:
            css[column] = frame[column].map(lambda v: tint(v, scales[column]))
        return css

    return display_df.style.apply(_paint, axis=None)

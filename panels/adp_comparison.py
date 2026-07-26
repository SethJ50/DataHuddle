from shiny import module, ui, render, reactive
from scoring import ScoringFormat
from registry import Position
from presentation.adp_comparison_view import AdpComparisonView


@module.ui
def adp_comparison_ui(ctx):
    """
    Purpose: Builds the page layout -- a position filter dropdown, a
        scoring-format toggle, and the table itself.

    Parameters:
        ctx (AppContext): not queried directly here (unlike Player Profile's
            dropdown), since the position/format choices are fixed lists
            rather than data pulled from ctx. Still accepted so this
            function's signature matches every other panel's pattern.

    Returns:
        A Shiny UI element to embed in the app's navigation.
    """

    # Build {"All": "All", "QB": "QB", "RB": "RB", ...} for the dropdown.
    position_choices = {"All": "All"}
    for p in Position:
        position_choices[p.value] = p.value

    # Build Shiny UI Element. Controls are grouped in a tight, centered flex
    # row (rather than spread across the card's full width) so they sit
    # visually consolidated directly above the table, which is centered the
    # same way below.
    return ui.card(
        ui.div(
            ui.input_select("position", "Position", choices=position_choices, selected="All", width="140px"),
            ui.input_select(
                "scoring_format",
                "Scoring Format",
                choices={
                    ScoringFormat.HALF_PPR.value: "Half PPR",
                    ScoringFormat.FULL_PPR.value: "Full PPR",
                },
                selected=ScoringFormat.HALF_PPR.value,
                width="160px",
            ),
            ui.input_text("search", "Search Player", placeholder="Search by name...", width="220px"),
            style="display: flex; gap: 24px; align-items: flex-end; justify-content: center; flex-wrap: wrap;",
        ),
        ui.div(
            ui.output_data_frame("adp_table"),
            style="display: flex; justify-content: center;",
        ),
        full_screen=True,
    )


@module.server
def adp_comparison_server(input, output, session, ctx):
    """
    Purpose: Wires the page's reactive behavior -- recomputing the
        comparison table when the scoring format changes, and re-filtering
        it when the position dropdown changes.

    Parameters:
        input, output, session: supplied automatically by Shiny.
        ctx (AppContext): provides ctx.adp_comparison_service.
    """

    @reactive.calc
    def comparison_data():
        """
        Purpose: Runs the expensive step -- loading all three platforms and
            resolving player identities -- only when the scoring-format
            toggle changes.
        Returns: pd.DataFrame, AdpComparisonService.compare()'s output.
        """
        fmt = ScoringFormat(input.scoring_format())
        return ctx.adp_comparison_service.compare(fmt)

    @reactive.calc
    def display_data():
        """
        Purpose: Runs the cheap step -- filtering by position and shaping
            for display -- every time the position dropdown changes,
            without redoing the expensive comparison_data() step.
        Returns: pd.DataFrame, ready for the grid.
        """
        return AdpComparisonView.shape(comparison_data(), input.position())

    @reactive.calc
    def filtered_data():
        """
        Purpose: Applies the single name-search box above the table, on top
            of whatever display_data() already produced.
        Returns: pd.DataFrame, filtered to rows whose Player name contains
            the search text (case-insensitive substring match). Returns the
            data unfiltered if the search box is empty.
        """
        df = display_data()
        query = input.search().strip()
        if query:
            df = df[df["Player"].str.contains(query, case=False, na=False)]
        return df

    @render.data_frame
    def adp_table():
        """
        Purpose: Renders the final interactive table.
        Returns: a shiny.render.DataGrid, sized to its content
            (width="fit-content") rather than stretching full-width.
            Column-header click-to-sort is built into DataGrid; the dark
            header / alternating rows / compact padding look comes from
            www/table_style.css (included once, app-wide, in app.py),
            styled to match presentation/table_style.py's static-table look.
        """
        return render.DataGrid(filtered_data(), filters=False, width="fit-content", height="700px")

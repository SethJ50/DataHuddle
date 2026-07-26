from shiny import module, ui


@module.ui
def fpts_vs_xfp_ui():
    return ui.card("Actual FPTS vs. XFP — coming soon")


@module.server
def fpts_vs_xfp_server(input, output, session):
    pass

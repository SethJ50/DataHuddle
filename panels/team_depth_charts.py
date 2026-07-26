from shiny import module, ui


@module.ui
def team_depth_charts_ui():
    return ui.card("Team Depth Charts — coming soon")


@module.server
def team_depth_charts_server(input, output, session):
    pass

from shiny import module, ui


@module.ui
def defensive_performance_ui():
    return ui.card("Defensive Performance — coming soon")


@module.server
def defensive_performance_server(input, output, session):
    pass

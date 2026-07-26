from shiny import module, ui


@module.ui
def pace_of_play_ui():
    return ui.card("Pace of Play — coming soon")


@module.server
def pace_of_play_server(input, output, session):
    pass

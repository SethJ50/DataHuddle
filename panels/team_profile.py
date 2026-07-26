from shiny import module, ui


@module.ui
def team_profile_ui():
    return ui.card("Team Profile — coming soon")


@module.server
def team_profile_server(input, output, session):
    pass

from shiny import module, ui


@module.ui
def draft_plans_ui():
    return ui.card("Draft Plans — coming soon")


@module.server
def draft_plans_server(input, output, session):
    pass

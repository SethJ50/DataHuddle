from shiny import App, ui

from app_context import AppContext

from panels.player_profile import player_profile_ui, player_profile_server
from panels.team_profile import team_profile_ui, team_profile_server
from panels.adp_comparison import adp_comparison_ui, adp_comparison_server
from panels.team_depth_charts import team_depth_charts_ui, team_depth_charts_server
from panels.draft_plans import draft_plans_ui, draft_plans_server
from panels.fpts_vs_xfp import fpts_vs_xfp_ui, fpts_vs_xfp_server
from panels.pace_of_play import pace_of_play_ui, pace_of_play_server
from panels.defensive_performance import defensive_performance_ui, defensive_performance_server

SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]
ctx = AppContext(SEASONS)

app_ui = ui.page_navbar(
    ui.nav_panel(
        "Home"
    ),
    ui.nav_menu(
        "General",
        ui.nav_panel(
            "Player Profile",
            player_profile_ui("player_profile", ctx)
        ),
        ui.nav_panel(
            "Team Profile",
            team_profile_ui("team_profile")
        )
    ),
    ui.nav_menu(
        "Pre-Draft",
        ui.nav_panel(
            "ADP Platform Comparison",
            adp_comparison_ui("adp_comparison", ctx)
        ),
        ui.nav_panel(
            "Team Depth Charts",
            team_depth_charts_ui("team_depth_charts")
        ),
        ui.nav_panel(
            "Draft Plans",
            draft_plans_ui("draft_plans")
        )
    ),
    ui.nav_menu(
        "Daily Fantasy",
        ui.nav_panel(
            "Actual FPTS vs. XFP",
            fpts_vs_xfp_ui("fpts_vs_xfp")
        ),
        ui.nav_panel(
            "Pace of Play",
            pace_of_play_ui("pace_of_play")
        ),
        ui.nav_panel(
            "Defensive Performance",
            defensive_performance_ui("defensive_performance")
        )
    ),
    title = "DataHuddle",
    id = "homePage",
    header = ui.include_css("www/table_style.css"),
)


def server(input, output, session):
    player_profile_server("player_profile", ctx)
    team_profile_server("team_profile")
    adp_comparison_server("adp_comparison", ctx)
    team_depth_charts_server("team_depth_charts")
    draft_plans_server("draft_plans")
    fpts_vs_xfp_server("fpts_vs_xfp")
    pace_of_play_server("pace_of_play")
    defensive_performance_server("defensive_performance")


app = App(app_ui, server)

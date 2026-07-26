from shiny import module, ui, render, reactive

from presentation.gamelog_view import GameLogView


@module.ui
def player_profile_ui(ctx):
    return ui.layout_columns(
        ui.card(
            ui.output_ui("pp_player_pic"),
            ui.div(
                ui.input_selectize(
                    id="pp_player",
                    label=None,
                    choices=ctx.roster_service.player_names(),
                    selected="00-0037239",  # Chris Olave
                    width="100%",
                ),
                style="margin-top: -10px;",
            ),
            full_screen=False,
            height="750px",
        ),
        ui.card(
            ui.card_header("Box Scores"),
            ui.output_data_frame("box_score_table"),
            height="750px",
        ),
        col_widths={"sm": (3, 9)},
        fill=True,
    )


@module.server
def player_profile_server(input, output, session, ctx):

    @render.ui
    def pp_player_pic():
        canonical_id = input.pp_player()
        selected_name = ctx.player_directory.get_display_name(canonical_id) or "Player"

        headshot_url = ctx.player_directory.get_headshot(canonical_id)

        return ui.div(
            ui.tags.img(
                src=headshot_url,
                style="max-width: 100%; height: auto; max-height: 200px; display: block; margin: 0 auto;",
                alt=f"{selected_name} Headshot",
                onerror="this.onerror=null; this.src='www/defaultPlayer.png';",
            ),
            style="width: 100%; text-align: center;",
        )

    @reactive.calc
    def player_gamelog_data():
        canonical_id = input.pp_player()
        return GameLogView.shape(
            ctx.player_directory.get_gamelog(canonical_id),
            ctx.player_directory.get_position(canonical_id),
        )

    @render.data_frame
    def box_score_table():
        return render.DataGrid(player_gamelog_data(), width="fit-content", height="650px")
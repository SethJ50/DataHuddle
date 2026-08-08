"""The catalogue of columns the Cheat Sheet can show, and which start on.

The whole page is one read of the player-week table, so the work is deciding what
to put in front of somebody. That is a judgement, not a calculation, so it lives
here as a declaration rather than being spread through the page.

Adding a statistic anywhere upstream becomes a new checkbox by adding one line to
`GROUPS` below. Nothing in the page has to change.
"""

from presentation.dfs_gamelog import Column

IDENTITY = [
    Column("name", "Player", None),
    Column("position", "Pos", None),
    Column("team", "Team", None),
    Column("opponent", "Opp", None),
]
"""Always shown. Without these a row is a number with nobody attached to it."""


POSITION_FILTERS = ("QB", "RB", "WR", "TE", "FLX", "DST")
"""The positions the sheet can be narrowed to, ONE AT A TIME.

`FLX` is the flex pool -- backs, receivers and tight ends together -- and is the
only one that is not a real position. It exists because a flex decision is a
comparison ACROSS positions, and the whole point of the sheet is that you cannot
make it while looking at three separate tables.
"""

FLEX_POSITIONS = ("RB", "WR", "TE")
"""Who is in the flex pool."""


SLATE_ONLY_GROUPS = ("Slate", "Passing (per game)", "Rushing (per game)",
                     "Receiving (per game)", "Defence (per game)", "Matchup",
                     "History")
"""Groups that only make sense for a slate that has not been played.

They all describe trailing form or a fixture, and for a week already played the
sheet shows what actually happened instead.
"""

SLATE_GROUP = "Slate"
"""The group holding the salary columns.

Shown only when a slate is actually loaded for the week being looked at. Offering
a salary column for a week nobody has prices for would be offering an empty
column.
"""


SLATE_PRICE = [
    Column("salary", "Salary", "$%d",
           "What this player costs on the site selected above."),
    Column("value_per_1k", "Val", "%.2f",
           "Recent points per $1,000 of salary. The only way to compare a "
           "$9,100 back with a $4,200 receiver."),
]
"""What a player costs, shown whatever position is selected."""


RUSHING_FORM = [
    Column("form_carries", "Att/g", "%.1f", "Carries per game."),
    Column("form_rushing_yards", "RuYd/g", "%.1f"),
    Column("form_rushing_tds", "RuTD/g", "%.2f"),
    Column("form_inside_5_carries", "In5/g", "%.2f",
           "Carries from inside the five-yard line per game. The most valuable "
           "carry there is -- it separates two backs who look identical on "
           "volume."),
    Column("form_rushing_epa", "RuEPA", "%.2f",
           "Expected points added by his carries, per game."),
]

RECEIVING_FORM = [
    Column("form_targets", "Tgt/g", "%.1f"),
    Column("form_receptions", "Rec/g", "%.1f"),
    Column("form_receiving_yards", "ReYd/g", "%.1f"),
    Column("form_receiving_tds", "ReTD/g", "%.2f"),
    Column("form_air_yards", "aDOT", "%.1f",
           "Average depth of target -- how far downfield he is thrown to."),
    Column("form_receiving_air_yards", "AirYd/g", "%.1f",
           "Air yards per game: how much downfield volume is aimed at him."),
    Column("form_receiving_epa", "ReEPA", "%.2f"),
    Column("form_target_share", "Tgt%", "%.0f%%", "Share of his team's targets.",
           scale=100),
    Column("form_wopr", "WOPR", "%.2f",
           "Target share and air-yards share combined. Higher means a bigger "
           "role in the passing game."),
]

PASSING_FORM = [
    Column("form_attempts", "Att/g", "%.1f"),
    Column("form_passing_yards", "PaYd/g", "%.1f"),
    Column("form_passing_tds", "PaTD/g", "%.2f"),
    Column("form_passing_air_yards", "AirYd/g", "%.1f"),
    Column("form_passing_epa", "PaEPA", "%.2f"),
]

DEFENCE_FORM = [
    Column("form_points_allowed", "PA/g", "%.1f",
           "Points allowed per game. The biggest single component of a "
           "defence's score."),
    Column("form_sacks", "Sk/g", "%.1f"),
    Column("form_interceptions", "Int/g", "%.2f"),
    Column("form_fumble_recoveries", "FR/g", "%.2f",
           "Fumbles RECOVERED per game. Forcing one the other side falls on "
           "scores nothing."),
    Column("form_defensive_tds", "TD/g", "%.2f"),
]

POINTS_FORM = [
    Column("form_points", "FP/g", "%.1f"),
    Column("form_expected_points", "xFP/g", "%.1f"),
]

HISTORY_GAMES = 10
"""How many past games the history columns lay out."""

HISTORY = [
    Column(f"L{n}", f"L{n}", "%.1f",
           f"Fantasy points {n} game{'s' if n > 1 else ''} ago. Hover the cell "
           "for the week and opponent.")
    for n in range(1, HISTORY_GAMES + 1)
]
"""One column per recent game, most recent first.

An average says a player scores fourteen a game. These say whether that is
fourteen every week or two forties and eight blanks -- and for a one-week contest
those are completely different players.
"""


MATCHUP = [
    Column("def_rank_rush", "vsRush", "%.0f",
           "Where his opponent's defence ranks against the run over the same "
           "window, 1 being stingiest. A short window, so read it as a steer "
           "rather than a fine distinction."),
    Column("def_rank_pass", "vsPass", "%.0f",
           "Where his opponent's defence ranks against the pass over the same "
           "window, 1 being stingiest."),
]



GROUPS = {
    SLATE_GROUP: SLATE_PRICE + [
        Column("form_points", "FP/g", "%.1f",
               "Fantasy points per game over his recent form window."),
        Column("form_expected_points", "xFP/g", "%.1f",
               "Expected points per game over the same window."),
        Column("form_snap_share", "Snap%", "%.0f%%",
               "Snap share over the same window.", scale=100),
        Column("form_games", "Gms", "%d",
               "How many games the form figures are built from. Fewer than the "
               "window means he has not played that many."),
        Column("site_projection", "SiteProj", "%.1f",
               "The site's own average for this player. Their number, not "
               "ours -- useful as a second opinion."),
        Column("injury_status", "Inj", None,
               "The injury flag the site published with the slate."),
    ],
    "Scoring": [
        Column("total_fantasy_points", "FP", "%.1f",
               "Fantasy points scored, in the scoring chosen above."),
        Column("total_fantasy_points_exp", "xFP", "%.1f",
               "What his opportunities were worth on average, whatever "
               "happened to them."),
        Column("points_gap", "Gap", "%+.1f",
               "Scored minus expected. Positive means he finished better than "
               "his chances deserved, which is either skill or luck."),
        Column("rush_fantasy_points_exp", "xRush", "%.1f"),
        Column("rec_fantasy_points_exp", "xRec", "%.1f"),
    ],
    "Volume": [
        Column("offense_snaps", "Snaps", "%d"),
        Column("snap_share", "Snap%", "%.0f%%",
               "Share of his offence's snaps. Usually moves before the "
               "production does.", scale=100),
        Column("targets", "Tgt", "%d"),
        Column("carries", "Car", "%d"),
        Column("red_zone_touches", "RZ", "%d",
               "Carries plus targets inside the twenty, whether or not they "
               "were caught."),
    ],
    "Share of offence": [
        Column("target_share", "Tgt%", "%.0f%%", "Share of his team's targets.",
               scale=100),
        Column("air_yards_share", "AY%", "%.0f%%",
               "Share of his team's air yards.", scale=100),
        Column("wopr", "WOPR", "%.2f",
               "Target share and air-yards share combined. Higher means a "
               "bigger role in the passing game."),
    ],
    "Production": [
        Column("receptions", "Rec", "%d"),
        Column("receiving_yards", "ReYd", "%d"),
        Column("rushing_yards", "RuYd", "%d"),
        Column("passing_yards", "PaYd", "%d"),
        Column("receiving_yards_after_catch", "YAC", "%d"),
    ],
    "Efficiency": [
        Column("receiving_epa", "recEPA", "%.2f",
               "Expected points added by his catches."),
        Column("rushing_epa", "rushEPA", "%.2f",
               "Expected points added by his carries."),
        Column("racr", "RACR", "%.2f",
               "Receiving yards per air yard. Above 1 is a yards-after-catch "
               "player."),
        Column("passing_cpoe", "CPOE", "%.1f",
               "Completion percentage over expected."),
    ],
    "Passing (per game)": PASSING_FORM,
    "Rushing (per game)": RUSHING_FORM,
    "Receiving (per game)": RECEIVING_FORM,
    "Defence (per game)": DEFENCE_FORM,
    "Matchup": MATCHUP,
    "History": HISTORY,
    "Tracking": [
        Column("avg_separation", "Sep", "%.2f",
               "Yards of separation at the catch point."),
        Column("avg_intended_air_yards", "aDOT", "%.1f",
               "Average depth of target."),
        Column("avg_cushion", "Cush", "%.2f",
               "Yards the defender gave him at the snap."),
        Column("rush_yards_over_expected_per_att", "RYOE", "%.2f",
               "Rushing yards over expected per carry."),
        Column("rushing_yards_after_contact_avg", "YACon", "%.2f",
               "Rushing yards after contact per carry."),
        Column("receiving_drop_pct", "Drop%", "%.1f%%", scale=100),
    ],
}
"""Every optional column, grouped by the question it answers.

Grouped rather than listed flat because forty checkboxes in one row is a wall.
The groups are also roughly the sources -- tracking numbers are the sparsest and
sit last, so their gaps do not read as the table being broken.
"""


DEFAULT_COLUMNS = ("total_fantasy_points", "total_fantasy_points_exp",
                   "points_gap", "snap_share", "targets", "target_share")
"""What is shown for a week that has been PLAYED, before anybody chooses.

DELIBERATELY SHORT. The Draft Runner's console reached fourteen columns and
became harder to read rather than easier -- more numbers per row does not help
you decide faster, it helps you hesitate. Everything else is one checkbox away.
"""

SLATE_DEFAULTS = ("salary", "value_per_1k", "form_points",
                  "form_expected_points", "form_snap_share", "injury_status")
"""What is shown for a slate that has NOT been played.

A different set, because a slate has no results yet -- the question there is what
a player costs and what he has been doing, not what he scored. Kept just as short
as the other one, for the same reason.
"""


# What each position is worth looking at, as trailing per-game averages. These
# are the columns turned on when that position is selected; everything else in
# the catalogue is still one checkbox away.

def position_defaults(position):
    """Choose which columns to switch on for the position being looked at.

    Steps:
        1. Start with what a player costs and what he is worth.
        2. Add the columns that describe how that position actually scores --
           carries for a back, targets for a receiver, both for a flex.
        3. Add the matchup ranks, except for a defence, which has no opposing
           defence to face.
        4. Add the game-by-game history, which is the same question the averages
           answer, asked without the averaging.

    Args:
        position: One of `POSITION_FILTERS` above.

    Returns:
        tuple: The field names to switch on.
    """
    if position == "DST":
        return tuple(column.field for column in
                     SLATE_PRICE + DEFENCE_FORM + [POINTS_FORM[0]] + HISTORY)

    if position == "QB":
        shape = PASSING_FORM + RUSHING_FORM[:3]
    elif position == "RB":
        shape = RUSHING_FORM + RECEIVING_FORM[:3]
    elif position == "FLX":
        shape = RUSHING_FORM[:4] + RECEIVING_FORM
    else:
        shape = RECEIVING_FORM

    return tuple(column.field for column in
                 SLATE_PRICE + shape + POINTS_FORM + MATCHUP + HISTORY)


def every_column():
    """List every optional column, in the order the groups declare them.

    Steps:
        1. Walk the groups and flatten their columns into one list.

    Returns:
        list: The `Column` records, group order preserved.
    """
    return [column for columns in GROUPS.values() for column in columns]


def columns_by_field():
    """Build a lookup from a column's data name to its record.

    Steps:
        1. Index every optional column by the field it reads.

    Returns:
        dict: Field name to `Column`.
    """
    return {column.field: column for column in every_column()}


def build(frame, chosen):
    """Cut the slate down to the chosen columns, ready to show.

    Steps:
        1. Put the identity columns first, then the chosen ones in the order the
           catalogue declares them rather than the order they were ticked -- so
           the table's shape stays familiar however somebody arrives at it.
        2. Drop any column the data does not actually have, which happens when a
           source was unavailable for that season.
        3. Apply each column's own scale, turning the fractions into
           percentages. Only the columns that ARE fractions carry one.

    Args:
        frame: The slate, from `services.dfs_player_service.slate`.
        chosen: The field names to show, in any order.

    Returns:
        tuple: `(table, columns)` -- the rows to draw and the `Column` records
            behind them, so the caller can build matching column settings.
    """
    wanted = set(chosen)
    catalogue = [column for column in every_column() if column.field in wanted]

    columns = [column for column in IDENTITY + catalogue
               if column.field in frame.columns]
    table = frame[[column.field for column in columns]].copy()

    for column in columns:
        if column.scale != 1.0:
            table[column.field] = table[column.field] * column.scale

    return table, columns

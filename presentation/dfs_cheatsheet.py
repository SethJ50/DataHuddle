"""The catalogue of columns the Cheat Sheet can show, and which start on.

The whole page is one read of the player-week table, so the work is deciding what
to put in front of somebody. That is a judgement, not a calculation, so it lives
here as a declaration rather than being spread through the page.

Adding a statistic anywhere upstream becomes a new checkbox by adding one line to
`GROUPS` below. Nothing in the page has to change.
"""

import pandas as pd

from presentation.dfs_gamelog import Column

IDENTITY = [
    Column("name", "Player", None, width=140),
    Column("position", "Pos", None),
    Column("team", "Team", None),
    Column("opponent", "Opp", None),
]
"""Always shown. Without these a row is a number with nobody attached to it."""

SLATE_IDENTITY = IDENTITY + [
    Column("salary", "Salary", "$%d",
           "What this player costs on the site selected above."),
]
"""The locked block for a slate. Salary is pinned rather than optional because
every other number on the row is only meaningful against it -- but a week already
played has no prices, so it cannot simply live in IDENTITY."""

FANTASY = [
    Column("form_points", "FP/g", "%.1f", "Fantasy points per game over his recent form window."),
    Column("form_expected_points", "xFP/g", "%.1f", "Expected points per game over the same window."),
    Column("value_per_1k", "Val", "%.2f",
           "Recent points per $1,000 of salary. The only way to compare a "
           "$9,100 back with a $4,200 receiver."),
]


POSITION_FILTERS = ("QB", "RB", "WR", "TE", "FLX", "DST")
"""The positions the sheet can be narrowed to, ONE AT A TIME.

`FLX` is the flex pool -- backs, receivers and tight ends together -- and is the
only one that is not a real position. It exists because a flex decision is a
comparison ACROSS positions, and the whole point of the sheet is that you cannot
make it while looking at three separate tables.
"""

FLEX_POSITIONS = ("RB", "WR", "TE")
"""Who is in the flex pool."""


SLATE_GROUP = "Slate"
"""The group holding the slate's own metadata.

Shown only when a slate is actually loaded for the week being looked at. Offering
these for a week nobody has prices for would be offering empty columns.
"""


PASSING_FORM = [
    Column("form_attempts", "Att", "%.1f"),
    Column("form_completions", "Cmp", "%.1f"),
    Column("form_passing_yards", "PaYd", "%.1f"),
    Column("form_passing_tds", "PaTD", "%.2f"),
    # NOT form_interceptions -- that one is a DEFENCE's takeaways.
    Column("form_passing_interceptions", "Int", "%.2f"),
]

PASSING_ADVANCED = [
    Column("form_passing_epa", "PaEPA", "%.2f", "Expected points added by his throws, per game."),
    Column("form_passing_cpoe", "CPOE", "%.1f",
           "Completion percentage over expected: how often he completes passes "
           "compared with a typical quarterback attempting the same throws."),
    Column("form_completion_pct", "Cmp%", "%.1f%%",
           "Completion percentage, averaged per game over the form window. "
           "Each game counts once, so it is not total completions over total "
           "attempts.", scale=100),
    Column("form_avg_time_to_throw", "TT", "%.2f", "Seconds from snap to release."),
    Column("form_pacr", "PACR", "%.2f", "Passing yards per air yard thrown."),
    Column("form_aggressiveness", "Agg%", "%.1f%%", "Share of throws made into tight coverage."),
]

RUSHING_FORM = [
    Column("form_carries", "Car", "%.1f", "Carries per game."),
    Column("form_rushing_yards", "RuYd", "%.1f"),
    Column("form_rushing_tds", "RuTD", "%.2f"),
]

RUSHING_OPPS = [
    Column("form_carry_share", "Car%", "%.0f%%",
           "Share of his team's carries.", scale=100),
    Column("form_adj_opportunities", "AdjOpp", "%.1f",
           "Adjusted opportunities per game: carries plus twice targets. A "
           "target is worth about two carries, so this compares a pass-catching "
           "back with a between-the-tackles back on one number."),
    Column("form_red_zone_carries", "RZC", "%.2f",
           "Carries inside the twenty per game."),
    Column("form_red_zone_carry_share", "RZC%", "%.0f%%",
           "Share of his team's carries inside the twenty.", scale=100),
    Column("form_goal_line_carries", "GL", "%.2f",
           "Carries with a goal-to-go line to gain, per game."),
    Column("form_goal_line_carry_share", "GL%", "%.0f%%",
           "Share of his team's goal-to-go carries.", scale=100),
    Column("form_inside_5_carry_share", "In5%", "%.0f%%",
           "Share of his team's carries from inside the five.", scale=100),
    Column("form_inside_5_carries", "In5", "%.2f",
           "Carries from inside the five-yard line per game. The most valuable "
           "carry there is -- it separates two backs who look identical on volume."),
]

RUSHING_ADVANCED = [
    Column("form_rushing_epa", "RuEPA", "%.2f", "Expected points added by his carries, per game."),
    Column("form_efficiency", "Eff", "%.2f", "How directly he runs. Lower means less east-west movement."),
    Column("form_rushing_yards_before_contact_avg", "YBC", "%.2f",
           "Yards before contact per carry -- mostly a measure of his line."),
    Column("form_rushing_yards_after_contact_avg", "YACon", "%.2f",
           "Yards after contact per carry -- mostly a measure of him."),
    Column("form_rush_yards_over_expected_per_att", "RYOE", "%.2f",
           "Rushing yards over expected per carry, given the blocking and the box."),
    Column("form_percent_attempts_gte_eight_defenders", "Stack%", "%.1f%%",
           "Share of his carries against eight or more defenders in the box."),
]

RECEIVING_FORM = [
    Column("form_targets", "Tgt", "%.1f"),
    Column("form_receptions", "Rec", "%.1f"),
    Column("form_receiving_yards", "ReYd", "%.1f"),
    Column("form_receiving_tds", "ReTD", "%.2f"),
]

RECEIVING_OPPS = [
    Column("form_target_share", "Tgt%", "%.0f%%", "Share of his team's targets.", scale=100),
    Column("form_red_zone_targets", "RZT", "%.2f",
           "Targets inside the twenty per game."),
    Column("form_red_zone_target_share", "RZT%", "%.0f%%",
           "Share of his team's targets inside the twenty.", scale=100),
    Column("form_inside_5_targets", "In5T", "%.2f",
           "Targets from inside the five per game."),
    Column("form_inside_5_target_share", "In5T%", "%.0f%%",
           "Share of his team's targets from inside the five.", scale=100),
    Column("form_end_zone_targets", "EZT", "%.2f",
           "Targets thrown as far as the goal line per game -- a shot at a "
           "touchdown, caught or not."),
    Column("form_end_zone_target_share", "EZT%", "%.0f%%",
           "Share of his team's end-zone targets.", scale=100),
    Column("form_receiving_air_yards", "AirYd", "%.1f",
           "Air yards per game: how much downfield volume is aimed at him."),
    Column("form_air_yards_share", "AY%", "%.0f%%",
           "Share of his team's air yards, from play-by-play.", scale=100),
    Column("form_percent_share_of_intended_air_yards", "iAY%", "%.1f%%",
           "Next Gen Stats' own share of intended air yards. Measures nearly the "
           "same thing as AY% from a different source -- when they disagree, the "
           "sample is small."),
    Column("form_receiving_yards_after_catch", "YAC", "%.1f", "Receiving yards after the catch, per game."),
    Column("form_wopr", "WOPR", "%.2f",
           "Target share and air-yards share combined. Higher means a bigger role."),
]

RECEIVING_ADVANCED = [
    Column("form_receiving_epa", "ReEPA", "%.2f", "Expected points added by his catches, per game."),
    Column("form_avg_cushion", "Cush", "%.2f", "Yards the defender gave him at the snap."),
    Column("form_avg_separation", "Sep", "%.2f", "Yards of separation at the catch point."),
    # `form_air_yards`, NOT `form_avg_intended_air_yards`. The form builder
    # gives this one a shorter name of its own -- see the `skip` set in
    # services/dfs_salary_service.py.
    Column("form_air_yards", "aDOT", "%.1f", "Average depth of target."),
    Column("form_avg_yac", "aYAC", "%.1f", "Average yards after catch per reception."),
    Column("form_avg_expected_yac", "xYAC", "%.1f",
           "Yards after catch a typical receiver would gain from the same catches. "
           "Compare with aYAC beside it."),
    Column("form_receiving_drop_pct", "Drop%", "%.1f%%", scale=100),
]

DEFENCE_FORM = [
    Column("form_points_allowed", "PA/g", "%.1f",
           "Points allowed per game. The biggest single component of a "
           "defense's score."),
    Column("form_sacks", "Sk/g", "%.1f"),
    Column("form_interceptions", "Int/g", "%.2f"),
    Column("form_fumble_recoveries", "FR/g", "%.2f",
           "Fumbles RECOVERED per game. Forcing one the other side falls on "
           "scores nothing."),
    Column("form_defensive_tds", "TD/g", "%.2f"),
]

DEFENCE_ALLOWED = [
    Column("def_fpts_per_rush", "FP/Ru", "%.2f",
           "Fantasy points his opponent's defense has allowed to running backs "
           "per rush faced, over the same window. HIGHER is a better matchup."),
    Column("def_epa_per_rush", "EPA/Ru", "%.3f",
           "Expected points added against that defense per rush. Measures how "
           "well it plays rather than how much it pays out, so it can disagree "
           "with FP/Ru -- and the disagreements are the interesting ones. "
           "HIGHER is a better matchup."),
    Column("def_fpts_per_pass", "FP/Pa", "%.2f",
           "Fantasy points allowed to receivers and tight ends per pass faced. "
           "HIGHER is a better matchup."),
    Column("def_epa_per_pass", "EPA/Pa", "%.3f",
           "Expected points added against that defense per pass. HIGHER is a "
           "better matchup."),
]


HISTORY_GAMES = 5
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

TEAM_TOTAL = Column(
    "implied_total", "ImpTot", "%.1f",
    "How many points his TEAM is expected to score -- the game total split "
    "between the two sides, with the favourite getting the larger share. The "
    "most useful single Vegas number: it is what Tot and Line mean once you "
    "stop doing the arithmetic yourself.")

OPPONENT_TOTAL = Column(
    "opp_implied_total", "OppTot", "%.1f",
    "How many points the OPPOSING team is expected to score. The right way "
    "round for a defense, which scores by holding that number down.")

GAME_LINES = [
    Column("game_total", "Tot", "%.1f",
           "The game's over/under, for BOTH teams together."),
    Column("game_spread", "Line", "%+.1f",
           "His team's spread, written the way a sportsbook does -- NEGATIVE "
           "means favoured."),
]

MATCHUP = [TEAM_TOTAL, OPPONENT_TOTAL] + GAME_LINES + DEFENCE_ALLOWED
"""This week's fixture, from the betting market.

NOT `form_` columns: these describe the game about to be played rather than an
average of past ones, so they are joined onto the board directly.

Both implied totals are offered as checkboxes, because either is worth a look
whatever you are picking. Which one is switched on BY DEFAULT is the part that
differs -- see the two subsets below.
"""

MATCHUP_OFFENCE = DEFENCE_ALLOWED + [TEAM_TOTAL] + GAME_LINES
"""Defaulted on for every position that scores by gaining yards."""

MATCHUP_DEFENCE = [OPPONENT_TOTAL] + GAME_LINES
"""Defaulted on for a DST, which scores by stopping the OTHER offence.

Named rather than sliced out of MATCHUP by index, so reordering that list cannot
silently hand a defence its own offence's total -- a mistake that inverts the
column while still looking like a plausible number.
"""


GROUPS = {
    # --- a slate that has NOT been played: trailing form and the fixture -----
    SLATE_GROUP: [
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
    "Fantasy": FANTASY,
    "Passing": PASSING_FORM,
    "Passing (Adv)": PASSING_ADVANCED,
    "Rushing": RUSHING_FORM,
    "Rushing (Opps)": RUSHING_OPPS,
    "Rushing (Adv)": RUSHING_ADVANCED,
    "Receiving": RECEIVING_FORM,
    "Receiving (Opps)": RECEIVING_OPPS,
    "Receiving (Adv)": RECEIVING_ADVANCED,
    "Defense": DEFENCE_FORM,
    "Matchup": MATCHUP,
    "History": HISTORY,

    # --- a week already PLAYED: what actually happened -----------------------
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
NO FIELD MAY APPEAR TWICE: the page keys each checkbox on its field, so a repeat
raises a duplicate-key error rather than drawing two boxes.
"""

SLATE_ONLY_GROUPS = ("Slate", "Fantasy", "Passing", "Passing (Adv)", "Rushing",
                     "Rushing (Opps)", "Rushing (Adv)", "Receiving",
                     "Receiving (Opps)", "Receiving (Adv)", "Defense",
                     "Matchup", "History")
"""Groups that only make sense for a slate that has not been played.

They all describe trailing form or a fixture, and for a week already played the
sheet shows what actually happened instead. Everything NOT listed here is a
played-week group, so adding a group means deciding which half it belongs to.
"""


DEFAULT_COLUMNS = ("total_fantasy_points", "total_fantasy_points_exp",
                   "points_gap", "snap_share", "targets", "target_share")
"""What is shown for a week that has been PLAYED, before anybody chooses.

DELIBERATELY SHORT. The Draft Runner's console reached fourteen columns and
became harder to read rather than easier -- more numbers per row does not help
you decide faster, it helps you hesitate. Everything else is one checkbox away.
"""

DEFAULT_COLUMN_WIDTH = 55

# What each position is worth looking at, as trailing per-game averages. These
# are the columns turned on when that position is selected; everything else in
# the catalogue is still one checkbox away.

def position_defaults(position):
    """Choose which columns to switch on for the position being looked at.

    Salary is NOT in here: it is pinned to the left of every row by
    `SLATE_IDENTITY` above rather than being an optional column.

    Steps:
        1. Start with what a player is worth -- points, expected points, and
           value against his price.
        2. Add the columns that describe how that position actually scores:
           passing for a quarterback, carries for a back, targets for a
           receiver, and both for a flex.
        3. Add the game-by-game history, which is the same question the averages
           answer, asked without the averaging.

    Args:
        position: One of `POSITION_FILTERS` above.

    Returns:
        tuple: The field names to switch on.
    """
    # A defence gets the OPPOSING offence's implied total; everybody else gets
    # their own. Same game, opposite side of it.
    if position == "DST":
        base = DEFENCE_FORM + [FANTASY[0]] + MATCHUP_DEFENCE
    elif position == "QB":
        base = FANTASY + PASSING_FORM + PASSING_ADVANCED[:3] + MATCHUP_OFFENCE
    elif position == "RB":
        base = FANTASY + RUSHING_FORM + RUSHING_OPPS[:3] + RECEIVING_FORM[:2] + MATCHUP_OFFENCE
    elif position == "FLX":
        base = FANTASY + RUSHING_FORM[:2] + RUSHING_OPPS[1:2] + RECEIVING_FORM + RECEIVING_OPPS[:1] + MATCHUP_OFFENCE
    else:
        base = FANTASY + RECEIVING_FORM + RECEIVING_OPPS[:2] + MATCHUP_OFFENCE

    return tuple(column.field for column in base + HISTORY)


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
        1. Choose the locked block. A frame carrying prices is a slate, so salary
           pins beside the player's name; a week already played has no prices and
           gets the shorter block.
        2. Put that block first, then the chosen columns in the order the
           catalogue declares them rather than the order they were ticked -- so
           the table's shape stays familiar however somebody arrives at it.
        3. Drop any column the data does not actually have, which happens when a
           source was unavailable for that season.
        4. Apply each column's own scale, turning the fractions into
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

    # Presence of a salary column is what tells a slate from a played week --
    # the same test the page uses to decide which groups to offer.
    locked = SLATE_IDENTITY if "salary" in frame.columns else IDENTITY

    columns = [column for column in locked + catalogue
               if column.field in frame.columns]
    table = frame[[column.field for column in columns]].copy()

    for column in columns:
        if column.scale != 1.0:
            table[column.field] = table[column.field] * column.scale

    # TWO-LEVEL HEADINGS. Streamlit draws the upper level of a MultiIndex as a
    # banner spanning the columns beneath it, which is the only way to title a
    # group of columns -- and unlike shading them, it costs no cell background,
    # leaving that free for conditional formatting.
    #
    # The locked columns get an empty group, which renders as no banner.
    group_of = {column.field: name
                for name, group in GROUPS.items() for column in group}
    table.columns = pd.MultiIndex.from_tuples(
        [(group_of.get(column.field, ""), column.label) for column in columns]
    )

    return table, columns

# Which columns carry a colour scale, and which end of them is good.
#
# OPT-IN: a column absent from here is never painted. Declared as one map rather
# than a field on each Column because there are over a hundred of them, and a
# list you can read top to bottom is easier to audit than the same decisions
# scattered through the catalogue.
#
# Ambiguous ones are deliberately ABSENT rather than guessed at -- see the note
# below the map.
SCALE_DIRECTIONS = {
    # --- what he is worth ---
    "form_points": "higher", "form_expected_points": "higher",
    "value_per_1k": "higher", "site_projection": "higher",
    "form_snap_share": "higher",

    # --- passing ---
    "form_attempts": "higher", "form_completions": "higher",
    "form_passing_yards": "higher", "form_passing_tds": "higher",
    "form_passing_interceptions": "lower",
    "form_passing_epa": "higher", "form_passing_cpoe": "higher",
    "form_completion_pct": "higher", "form_pacr": "higher",

    # --- rushing ---
    "form_carries": "higher", "form_rushing_yards": "higher",
    "form_rushing_tds": "higher", "form_carry_share": "higher",
    "form_adj_opportunities": "higher",
    "form_red_zone_carries": "higher", "form_red_zone_carry_share": "higher",
    "form_goal_line_carries": "higher", "form_goal_line_carry_share": "higher",
    "form_inside_5_carries": "higher", "form_inside_5_carry_share": "higher",
    "form_rushing_epa": "higher",
    # NGS "efficiency" counts east-west movement, so a SMALLER number is a more
    # direct runner. The one rushing column where lower wins.
    "form_efficiency": "lower",
    "form_rushing_yards_before_contact_avg": "higher",
    "form_rushing_yards_after_contact_avg": "higher",
    "form_rush_yards_over_expected_per_att": "higher",
    "form_percent_attempts_gte_eight_defenders": "lower",

    # --- receiving ---
    "form_targets": "higher", "form_receptions": "higher",
    "form_receiving_yards": "higher", "form_receiving_tds": "higher",
    "form_target_share": "higher",
    "form_red_zone_targets": "higher", "form_red_zone_target_share": "higher",
    "form_inside_5_targets": "higher", "form_inside_5_target_share": "higher",
    "form_end_zone_targets": "higher", "form_end_zone_target_share": "higher",
    "form_receiving_air_yards": "higher", "form_air_yards_share": "higher",
    "form_percent_share_of_intended_air_yards": "higher",
    "form_receiving_yards_after_catch": "higher", "form_wopr": "higher",
    "form_receiving_epa": "higher", "form_avg_separation": "higher",
    "form_air_yards": "higher", "form_avg_yac": "higher",
    "form_avg_expected_yac": "higher", "form_receiving_drop_pct": "lower",

    # --- defence ---
    "form_points_allowed": "lower", "form_sacks": "higher",
    "form_interceptions": "higher", "form_fumble_recoveries": "higher",
    "form_defensive_tds": "higher",

    # --- the fixture ---
    "implied_total": "higher", "game_total": "higher",
    "game_spread": "lower",          # a sportsbook line: negative is favoured
    "opp_implied_total": "lower",    # a DST wants the other offence held down
    "def_fpts_per_rush": "higher", "def_epa_per_rush": "higher",
    "def_fpts_per_pass": "higher", "def_epa_per_pass": "higher",

    # --- history ---
    **{f"L{n}": "higher" for n in range(1, HISTORY_GAMES + 1)},

    # --- a week already played ---
    "total_fantasy_points": "higher", "total_fantasy_points_exp": "higher",
    "points_gap": "higher", "rush_fantasy_points_exp": "higher",
    "rec_fantasy_points_exp": "higher",
    "offense_snaps": "higher", "snap_share": "higher", "targets": "higher",
    "carries": "higher", "red_zone_touches": "higher",
    "target_share": "higher", "air_yards_share": "higher", "wopr": "higher",
    "receptions": "higher", "receiving_yards": "higher",
    "rushing_yards": "higher", "passing_yards": "higher",
    "receiving_yards_after_catch": "higher",
    "receiving_epa": "higher", "rushing_epa": "higher", "racr": "higher",
    "passing_cpoe": "higher", "avg_separation": "higher",
    "avg_intended_air_yards": "higher", "avg_cushion": "higher",
    "rush_yards_over_expected_per_att": "higher",
    "rushing_yards_after_contact_avg": "higher",
    "receiving_drop_pct": "lower",
}
"""LEFT OUT ON PURPOSE, because there is no honest answer:

`salary` is a cost, not a virtue. `form_games` is sample size. `injury_status`
is text. `form_avg_time_to_throw` and `form_aggressiveness` cut both ways -- a
quick release is good until it means he is not looking downfield. `form_avg_cushion`
likewise: a defender playing off is an easy completion and a sign of no respect.

Add one here the day you decide which way it reads.
"""


def scale_directions(table, columns):
    """Match each of the table's headings to the direction that counts as good.

    The table's columns are `(group, label)` pairs while the directions above are
    keyed by field name, so something has to join the two. This does, using the
    `Column` records that `build` returns alongside the table.

    Steps:
        1. Walk the table's headings and the column records together -- `build`
           produces them in the same order.
        2. Keep only the columns that declared a direction.

    Args:
        table: `build`'s table, with two-level headings.
        columns: `build`'s column records, in the same order.

    Returns:
        dict: Maps a `(group, label)` heading to "higher" or "lower", ready for
            `color_scale` in presentation/st_tables.py.
    """
    return {heading: SCALE_DIRECTIONS[column.field]
            for heading, column in zip(table.columns, columns)
            if column.field in SCALE_DIRECTIONS}

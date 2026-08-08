"""Puts this week's prices next to what each player has been doing lately.

A salary file describes a slate that has not been played. Everything else in this
app describes games that have. Joining them on the week would match nothing, so
the join is on the PLAYER, and what comes back is his recent form beside what he
costs today.

That is also the honest version of the question: before kickoff, recent form is
all anybody has.
"""

import numpy as np
import pandas as pd

DEFAULT_TRAILING_GAMES = 5
"""How many recent games make up "lately" by default.

Enough to see past one quiet week, short enough to still describe now. A player
who changed role a month ago should look changed.
"""

VALUE_PER = 1000
"""Salary step a value figure is quoted against.

Points per $1,000 is the convention on both sites, and it is the only way to
compare a $9,100 back with a $4,200 receiver at all.
"""


def trailing_form(player_weeks, season, week,
                  games=DEFAULT_TRAILING_GAMES) -> pd.DataFrame:
    """Average each player's last few games, as of just before a given week.

    Steps:
        1. Keep only games played BEFORE the slate -- see the note, because
           including the slate week itself would be reading the answer.
        2. Order each player's games most recent first.
        3. Keep the last few for each.
        4. Average the numbers worth averaging, and count how many of those
           games came from an earlier season.

    Args:
        player_weeks: The table from `services.dfs_player_service.player_weeks`.
        season: The slate's season.
        week: The slate's week.
        games: How many recent games to average.

    Returns:
        pd.DataFrame: One row per player, with `canonical_id`, `form_games`,
            `form_seasons_back`, and averages for points, expected points, snap
            share, target share and air yards. Empty with those columns if
            nothing qualifies.

    Note:
        STRICTLY BEFORE THE SLATE. If the slate week has already been played --
        which it will have, for any week loaded after the fact -- including it
        would put the result inside the form used to predict it. The numbers
        would look wonderful and mean nothing.

        FORM CROSSES THE OFFSEASON, because in week one there is nothing else. A
        player who changed teams in March shows his old role, so
        `form_seasons_back` counts how many of the games came from a previous
        year and the pages say so.
    """
    columns = ["canonical_id", "form_games", "form_seasons_back",
               "form_points", "form_expected_points", "form_snap_share",
               "form_target_share", "form_air_yards"]

    if player_weeks.empty:
        return pd.DataFrame(columns=columns)

    rows = player_weeks.copy()

    # One sortable number per game, so "before this week" and "most recent
    # first" are both a single comparison rather than a pair of them.
    rows["_when"] = rows["season"].astype(int) * 100 + rows["week"].astype(int)
    before = rows[rows["_when"] < int(season) * 100 + int(week)]

    if before.empty:
        return pd.DataFrame(columns=columns)

    recent = (before.sort_values(["canonical_id", "_when"], ascending=[True, False])
              .groupby("canonical_id", as_index=False).head(games))

    def mean_of(column):
        """Average a column if the table has it, otherwise produce nothing."""
        return (column, "mean") if column in recent.columns else ("_when", "size")

    formed = recent.groupby("canonical_id", as_index=False).agg(
        form_games=("_when", "size"),
        earliest_season=("season", "min"),
        form_points=mean_of("total_fantasy_points"),
        form_expected_points=mean_of("total_fantasy_points_exp"),
        form_snap_share=mean_of("snap_share"),
        form_target_share=mean_of("target_share"),
        form_air_yards=mean_of("avg_intended_air_yards"),
    )
    formed["form_seasons_back"] = (int(season)
                                   - formed["earliest_season"].astype(int))

    # Every other number a page might want, averaged the same way and prefixed
    # so it cannot collide with the slate's own columns. Doing this generically
    # rather than naming each one means a statistic added upstream becomes
    # available here with no change at all.
    averaged = _average_everything(recent)
    formed = formed[columns].merge(averaged, on="canonical_id", how="left")

    return formed


def _average_everything(recent) -> pd.DataFrame:
    """Average every number in the form window, under a `form_` prefix.

    Steps:
        1. Take the numeric columns, minus the ones that are keys rather than
           measurements -- averaging a week number produces a number, and it
           means nothing.
        2. Average each by player.
        3. Prefix the results so they can sit beside the slate's own columns
           without either overwriting the other.

    Args:
        recent: The rows inside each player's form window.

    Returns:
        pd.DataFrame: `canonical_id` and one `form_<column>` per measurement.
    """
    # The keys are not measurements, and the five below already have a
    # friendlier name assigned by the caller -- averaging them again here would
    # produce two columns holding the same number under different names, which
    # pandas then suffixes into `form_snap_share_x` and `form_snap_share_y`.
    skip = {"season", "week", "_when", "canonical_id",
            "total_fantasy_points", "total_fantasy_points_exp",
            "snap_share", "target_share", "avg_intended_air_yards"}
    numeric = [column for column in recent.select_dtypes("number").columns
               if column not in skip]

    if not numeric:
        return recent[["canonical_id"]].drop_duplicates()

    averaged = recent.groupby("canonical_id", as_index=False)[numeric].mean()
    return averaged.rename(columns={column: f"form_{column}"
                                    for column in numeric})


def recent_history(player_weeks, season, week, games=10) -> pd.DataFrame:
    """Lay out each player's last few scores as one column per game.

    A single average says a player scores fourteen a game. Ten columns say
    whether that is fourteen every week or two forties and eight blanks, and for
    a one-week contest those are entirely different players.

    Steps:
        1. Keep the games played before the slate, as `trailing_form` above does
           and for the same reason.
        2. Number each player's games backwards from his most recent.
        3. Keep the last few and spread them across columns.
        4. Carry the week and opponent alongside, so a page can put them in a
           tooltip rather than in a heading nobody can read.

    Args:
        player_weeks: The table from `player_weeks`.
        season: The slate's season.
        week: The slate's week.
        games: How many games to lay out.

    Returns:
        pd.DataFrame: `canonical_id`, then `L1` … `L<games>` holding fantasy
            points, and a matching `L1_note` … carrying "week 14 vs SF". A player
            with fewer games has blanks in the later columns.

    Note:
        NUMBERED FROM THE MOST RECENT, not by week. Byes and missed games mean
        two players' week 14s are not comparable positions in a list, whereas
        "his last game" always is -- so every row lines up whatever each player's
        season looked like.
    """
    blank = pd.DataFrame(columns=["canonical_id"]
                         + [f"L{n}" for n in range(1, games + 1)])
    if player_weeks.empty:
        return blank

    rows = player_weeks.copy()
    rows["_when"] = rows["season"].astype(int) * 100 + rows["week"].astype(int)
    before = rows[rows["_when"] < int(season) * 100 + int(week)]

    if before.empty or "total_fantasy_points" not in before.columns:
        return blank

    # A row with no player id cannot be joined to a salary, and grouping by a
    # blank key produces a blank rank that will not convert to a number.
    before = before[before["canonical_id"].notna()]
    if before.empty:
        return blank

    before = before.sort_values(["canonical_id", "_when"],
                               ascending=[True, False])
    # `.astype(int)` matters: without it the rank is a float and the headers
    # come out as "L1.0", which no lookup elsewhere will ever match.
    before = before.assign(
        _rank=(before.groupby("canonical_id").cumcount() + 1).astype(int))
    before = before[before["_rank"] <= games]

    points = before.pivot(index="canonical_id", columns="_rank",
                          values="total_fantasy_points")
    points.columns = [f"L{n}" for n in points.columns]

    # The opponent is only ever a tooltip, so a table without one loses the
    # note rather than the whole history.
    opponent = (before["opponent"].fillna("?").astype(str)
                if "opponent" in before.columns else "?")
    notes = before.assign(
        _note="week " + before["week"].astype(str) + " vs " + opponent
    ).pivot(index="canonical_id", columns="_rank", values="_note")
    notes.columns = [f"L{n}_note" for n in notes.columns]

    return points.join(notes).reset_index()


def opponent_defence_ranks(repo, season, week, games, scoring) -> pd.DataFrame:
    """Rank every defence on what it has allowed lately, running and passing.

    A player's matchup, as two numbers. The columns exist so a cheap receiver
    facing the league's most generous secondary can be told apart from an
    identical one facing its stingiest.

    Steps:
        1. Work out which stretch of weeks the form window covers, using
           `_window_before` below -- the same stretch the player figures use.
        2. Ask `defensive_allowances` from services/dfs_team_service.py what each
           defence gave up over it, once for the run and once for the pass.
        3. Rank each, smallest first, since a defence that allows less is better.

    Args:
        repo: A `DfsReadRepo`.
        season: The slate's season.
        week: The slate's week.
        games: How many weeks back to look.
        scoring: Which scoring the allowances are measured in.

    Returns:
        pd.DataFrame: `opponent`, `def_rank_rush` and `def_rank_pass`, ranked so
            1 is the stingiest. Empty if the window holds no games.

    Note:
        MEASURED OVER THE SAME WINDOW AS THE PLAYER FIGURES, so everything on a
        row describes one stretch of time. That was a deliberate choice over a
        full-season rank, and it costs something: a five-week defensive sample is
        small, so these ranks move about far more than season-long ones would.
        Read them as a rough steer rather than a fine distinction.
    """
    from services.dfs_team_service import defensive_allowances, league_ranks

    window_season, weeks = _window_before(repo, season, week, games)
    if weeks is None:
        return pd.DataFrame(columns=["opponent", "def_rank_rush",
                                     "def_rank_pass"])

    ranked = {}
    for kind, positions, label in (("rush", ["RB"], "def_rank_rush"),
                                   ("pass", ["WR", "TE"], "def_rank_pass")):
        allowed = defensive_allowances(repo, window_season, weeks,
                                       positions=positions, play_kind=kind,
                                       scoring=scoring)
        if allowed.empty:
            continue
        allowed = league_ranks(allowed, ["points_per_play"],
                               lower_is_better=("points_per_play",))
        ranked[label] = allowed.set_index("team")["points_per_play_rank"]

    if not ranked:
        return pd.DataFrame(columns=["opponent", "def_rank_rush",
                                     "def_rank_pass"])

    table = pd.DataFrame(ranked).reset_index()
    return table.rename(columns={"index": "opponent", "team": "opponent"})


def _window_before(repo, season, week, games):
    """Find the stretch of weeks the form window covers.

    Steps:
        1. Collect the weeks that have plays, newest first.
        2. Keep those before the slate.
        3. Return the season and week range covering the most recent few.

    Args:
        repo: A `DfsReadRepo`.
        season: The slate's season.
        week: The slate's week.
        games: How many weeks back to reach.

    Returns:
        tuple: `(season, (first, last))`, or `(season, None)` if nothing
            qualifies.

    Note:
        Falls back to the LATEST season that has games before the slate, rather
        than spanning two. `defensive_allowances` takes one season at a time, and
        for a week-one slate the answer is simply all of the previous year --
        which is both the honest window and the larger sample.
    """
    plays = repo.pbp()
    if plays.empty:
        return season, None

    marked = plays[["season", "week"]].dropna().drop_duplicates()
    marked["_when"] = (marked["season"].astype(int) * 100
                       + marked["week"].astype(int))
    before = marked[marked["_when"] < int(season) * 100 + int(week)]

    if before.empty:
        return season, None

    window_season = int(before["season"].max())
    in_season = sorted(int(w) for w in
                       before.loc[before["season"] == window_season, "week"])
    kept = in_season[-games:] if len(in_season) > games else in_season

    return window_season, (kept[0], kept[-1])


def slate_board(salaries, player_weeks, season, week, site,
                games=DEFAULT_TRAILING_GAMES, defences=None, repo=None,
                scoring=None, history=0) -> pd.DataFrame:
    """Build the table behind a live slate: prices beside recent form.

    Steps:
        1. Keep the chosen site's rows for that slate.
        2. Work out everybody's recent form with `trailing_form` above.
        3. Attach it to the salaries by player, keeping every salaried row even
           when there is no form to attach -- see the note.
        4. Work out what each player's recent scoring is worth per $1,000, which
           is the only way to compare players at different prices.

    Args:
        salaries: One slate, from `DfsSalaryRepo.slate`.
        player_weeks: The table from `player_weeks`.
        season: The slate's season.
        week: The slate's week.
        site: Which site's prices to use.
        games: How many recent games to average.
        defences: The table from `services.dfs_dst_service.dst_weeks`, so team
            defences get form of their own. Omit it and DST rows carry a price
            and nothing else.
        repo: A `DfsReadRepo`, needed only for the opponent ranks. Omit it and
            those columns are absent.
        scoring: Which scoring the opponent ranks are measured in.
        history: How many past games to lay out one per column. Zero for none.

    Returns:
        pd.DataFrame: One row per salaried player, sorted dearest first, with the
            salary columns, the form columns, and `value_per_1k`.

    Note:
        A LEFT JOIN FROM THE SALARIES. Every player with a price appears, whether
        or not anything is known about him -- team defences never have a player
        id, a handful of names never resolve, and a rookie has no games at all.
        Dropping them would quietly remove rosterable players from a sheet whose
        whole job is to list who is rosterable.
    """
    board = salaries[salaries["site"] == site].copy() if "site" in salaries \
        else salaries.copy()

    if board.empty:
        return board.assign(**{column: [] for column in
                               ("form_games", "form_points", "value_per_1k")})

    # Defences are scored on something no player table records, so their form
    # is built separately and stacked on before the window is taken. They join
    # by TEAM rather than by player id, which they never have.
    combined = player_weeks
    if defences is not None and not defences.empty:
        as_players = defences.rename(columns={"team": "canonical_id"})
        as_players["position"] = "DST"
        combined = pd.concat([player_weeks, as_players], ignore_index=True)

    form = trailing_form(combined, season, week, games)

    # A defence's row is keyed by its abbreviation, so match on that where the
    # player id is missing -- which for a DST it always is.
    board["_join"] = board["canonical_id"].where(board["position"] != "DST",
                                                 board["team"])
    board = board.merge(form.rename(columns={"canonical_id": "_join"}),
                        on="_join", how="left")

    if history:
        past = recent_history(combined, season, week, history)
        board = board.merge(past.rename(columns={"canonical_id": "_join"}),
                            on="_join", how="left")

    if repo is not None:
        ranks = opponent_defence_ranks(repo, season, week, games, scoring)
        if not ranks.empty:
            board = board.merge(ranks, on="opponent", how="left")

    board = board.drop(columns=["_join"])

    salary = pd.to_numeric(board["salary"], errors="coerce")
    points = pd.to_numeric(board.get("form_points"), errors="coerce")

    with np.errstate(invalid="ignore", divide="ignore"):
        board["value_per_1k"] = np.where(salary > 0,
                                         points / (salary / VALUE_PER), np.nan)

    return board.sort_values("salary", ascending=False).reset_index(drop=True)

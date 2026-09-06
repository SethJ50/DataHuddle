"""Ready-made modeling tables, each cached to disk after the first build.

These are the starting points for a model. Every one of them goes through the
app's own services rather than hitting nflverse or MongoDB directly, so the
player-id resolution, scoring and player-universe rules a notebook gets are
exactly the ones the app shows -- a model trained here cannot quietly disagree
with the pages about who a player is.

Every function follows the same shape: call the service, hand the result to
`snapshot` from research/snapshots.py, and take a `refresh` argument for when
the underlying data has moved on.

Adding your own is meant to be easy -- copy one of these, change the body:

    def my_table(refresh=False):
        ctx = get_context()
        return snapshot("my_table", lambda: ...build it..., refresh=refresh)

Note:
    THE SNAPSHOT DOES NOT KNOW WHICH SEASONS IT COVERS. Each file is named after
    the function and its settings, not after `SEASONS`/`DFS_SEASONS` in
    research/context.py. Change either of those lists and you must pass
    `refresh=True` once, or you will read back the old span of seasons.
"""

import pandas as pd

from research.context import get_context
from research.snapshots import snapshot
from scoring import ScoringFormat
from services.dfs_scoring import DfsScoring
from services import dfs_player_service


def _key(value) -> str:
    """Turn a scoring setting into a short piece of text for a filename.

    Both scoring enums in this project inherit from `str`, so a member carries
    its own text in `.value` ("half_ppr", "FanDuel"). This lets the dataset
    functions accept either the enum member or the plain string.

    Steps:
        1. Read the `.value` attribute if there is one -- that is the enum case.
        2. Otherwise use the value as it came in, which is the plain-string
           case, and convert it to text.

    Args:
        value: A `ScoringFormat` member, a `DfsScoring` member, or the plain
            string version of either.

    Returns:
        str: The short text form, e.g. `"half_ppr"` or `"FanDuel"`.
    """
    return str(getattr(value, "value", value))


def dfs_player_weeks(scoring=DfsScoring.FANDUEL, refresh: bool = False) -> pd.DataFrame:
    """Get one row per player per week, with every Daily Fantasy source joined.

    The widest table in the project and the best default starting point for a
    weekly model. It is also the slowest to build -- several seasons of
    play-by-play -- which is exactly why it is snapshotted.

    Steps:
        1. Get the shared context with `get_context` from research/context.py.
        2. Ask `player_weeks` in services/dfs_player_service.py to build the
           table, which joins the box score, expected points, snap counts,
           tracking data, charting data and red-zone touches.
        3. Wrap that in `snapshot` from research/snapshots.py so the result is
           written to data/research/ and read back instantly next time.

    Args:
        scoring: Which site's scoring the points columns are in. Defaults to
            FanDuel. Each scoring gets its own snapshot file, so switching does
            not silently reuse the other one.
        refresh: Pass True to rebuild from source, e.g. after a week of games.

    Returns:
        pd.DataFrame: One row per player per week, covering DFS_SEASONS. Always
            has `canonical_id`, `name`, `position`, `team`, `opponent`,
            `season`, `week`, plus fantasy points, usage shares (target share,
            carry share), snap counts, red-zone touches and the tracking /
            charting columns. Anything a source had nothing to say about is
            blank rather than zero.
    """
    ctx = get_context()
    return snapshot(
        f"dfs_player_weeks_{_key(scoring)}",
        lambda: dfs_player_service.player_weeks(ctx.dfs_read_repo, scoring),
        refresh=refresh,
    )


def player_game_logs(refresh: bool = False) -> pd.DataFrame:
    """Get raw game-by-game stat lines across the long season-long history.

    Use this when you want more years than the Daily Fantasy table covers and
    do not need the joined-on extras. It is nflverse's own table, untouched --
    no fantasy points, no shares, no snap counts.

    Steps:
        1. Get the shared context with `get_context` from research/context.py.
        2. Ask `NflReadRepo.player_stats` for the game logs, which downloads
           them from nflverse the first time.
        3. Wrap that in `snapshot` so later kernels read the Parquet file.

    Args:
        refresh: Pass True to re-download from nflverse rather than reading the
            saved file.

    Returns:
        pd.DataFrame: One row per player per game across SEASONS (2020-2025 by
            default), with a wide set of passing, rushing and receiving columns
            plus `player_id`, `player_display_name`, `position`, `season`,
            `week` and `team`. Only players who actually appeared in a game are
            here -- a rookie who has not played yet has no row.
    """
    ctx = get_context()
    return snapshot(
        "player_game_logs",
        lambda: ctx.nfl_read_repo.player_stats(),
        refresh=refresh,
    )


def season_projections(analyst: str = None, refresh: bool = False) -> pd.DataFrame:
    """Get the app's season-long fantasy point projections, per player.

    The obvious thing to model AGAINST: a projection is somebody's guess at a
    season, and comparing it to what actually happened is where most season-long
    modeling starts.

    Steps:
        1. Get the shared context with `get_context` from research/context.py.
        2. Ask `ProjectionsService.get_own_projections` for either one analyst's
           numbers or the blend of all three, which is the app's default.
        3. Wrap that in `snapshot`, naming the file after the analyst so each
           gets its own.

    Args:
        analyst: One analyst's name (`"andy"`, `"mike"`, `"jason"`) for his
            numbers alone, or None, the default, for the blend of all three.
        refresh: Pass True to rebuild, e.g. after running
            scripts/load_data.py with new projection CSVs.

    Returns:
        pd.DataFrame: One row per player, with `canonical_id`, the projected
            stat line, and for each scoring format
            `fantasy_points_{fmt}_season` and `fantasy_points_{fmt}_per_game`.
            The blend also carries `_low` / `_high` / `_spread` per format and
            `n_analysts`, how many analysts rated that player. Only players in
            the app's roster universe appear.

    Raises:
        KeyError: If `MONGODB_URI` is not set -- projections live in MongoDB.
    """
    ctx = get_context()
    return snapshot(
        f"season_projections_{analyst or 'blend'}",
        lambda: ctx.projections_service.get_own_projections(analyst),
        refresh=refresh,
    )


def adp_table(fmt=ScoringFormat.HALF_PPR, refresh: bool = False) -> pd.DataFrame:
    """Get where each player is being drafted on ESPN, Sleeper and Yahoo.

    The market's opinion, one column per platform. Pair it with
    `season_projections` above and you have the two halves of most pre-draft
    value modeling: what a player is expected to do, and what he costs.

    Steps:
        1. Get the shared context with `get_context` from research/context.py.
        2. Ask `AdpComparisonService.compare` for the table, which resolves each
           platform's own player names to the app's canonical ids.
        3. Wrap that in `snapshot`, naming the file after the scoring format --
           ESPN and Sleeper publish genuinely different ADP per format.

    Args:
        fmt: The scoring format, as a `ScoringFormat` member. Defaults to
            half-PPR.
        refresh: Pass True to rebuild. ADP moves daily during draft season, so
            expect to use this often at that time of year.

    Returns:
        pd.DataFrame: One row per roster player with `canonical_id`,
            `display_name`, `headshot_url`, `position`, `espn_adp`,
            `yahoo_adp` and `sleeper_adp`. A platform that does not rank a
            player leaves that cell blank.

    Raises:
        KeyError: If `MONGODB_URI` is not set -- ADP lives in MongoDB.
    """
    ctx = get_context()
    return snapshot(
        f"adp_table_{_key(fmt)}",
        lambda: ctx.adp_comparison_service.compare(fmt),
        refresh=refresh,
    )

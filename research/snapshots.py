"""Saves an expensive DataFrame to disk so the next kernel restart is instant.

The problem this solves: building the Daily Fantasy player table takes a couple
of minutes, because it downloads several seasons of play-by-play from nflverse
and joins five sources together. That is fine once. It is not fine every time
you restart a notebook kernel, which while modeling you do constantly.

A "snapshot" is that finished table written to a Parquet file under
data/research/. Parquet is a table file format that stores columns rather than
rows: it keeps dtypes exactly (a date stays a date, unlike CSV), compresses
well, and reads back in a fraction of a second.

The folder is gitignored. Every file in it is regenerable from the function
that made it, so nothing here is precious -- delete it any time.
"""

from pathlib import Path

import pandas as pd

from research.context import PROJECT_ROOT

SNAPSHOT_DIR = PROJECT_ROOT / "data" / "research"
"""Where snapshot files live. Gitignored, and excluded from the Docker image --
this is research output, not app data."""


def snapshot_path(name: str) -> Path:
    """Work out the file a given snapshot name is stored at.

    Handy when you want to check a snapshot's size or age, or hand its path to
    something that reads Parquet itself.

    Steps:
        1. Join the snapshot folder and the name, adding a `.parquet` suffix.

    Args:
        name: The snapshot's name, e.g. `"dfs_player_weeks_FanDuel"`. Use plain
            words, digits and underscores; it becomes a filename.

    Returns:
        Path: The absolute path the snapshot is (or would be) written to. The
            file may not exist yet.
    """
    return SNAPSHOT_DIR / f"{name}.parquet"


def snapshot(name: str, build, refresh: bool = False) -> pd.DataFrame:
    """Return a saved table, building and saving it first if it is not there.

    The one function every dataset in datasets.py is wrapped in. Think of it as
    "run this slow thing, but only once".

    Steps:
        1. Work out the file path with `snapshot_path` above.
        2. If that file exists and `refresh` is False, read it with pandas and
           return it. This is the fast path and the usual one.
        3. Otherwise call `build()`, which is the slow function that actually
           assembles the table.
        4. Make sure the snapshot folder exists, write the result to Parquet,
           and return it.

    Args:
        name: What to call the file. Include anything that changes the CONTENT
            in the name -- scoring system, seasons, format -- or you will read
            back a table built under different settings. datasets.py does this
            by appending the argument, e.g. `"adp_table_half_ppr"`.
        build: A function taking no arguments that returns the DataFrame. Pass
            the function itself, not a call to it -- `lambda: expensive()`, not
            `expensive()` -- otherwise the slow work happens before `snapshot`
            is even entered, and caching buys you nothing.
        refresh: Pass True to ignore any existing file and rebuild from source.
            Do this when the underlying data has changed, such as after a week
            of games or a run of scripts/load_data.py.

    Returns:
        pd.DataFrame: The table, either read from disk or freshly built.

    Raises:
        ValueError: If pandas cannot write the frame to Parquet. Usually this
            means a column holds mixed types (e.g. numbers and strings), which
            Parquet cannot store. Fix the column's dtype in `build`.

    Example:
        weeks = snapshot("my_table", lambda: ctx.dfs_read_repo.player_stats())
    """
    path = snapshot_path(name)

    if path.exists() and not refresh:
        return pd.read_parquet(path)

    frame = build()

    # parents=True creates data/research/ if this is the first snapshot;
    # exist_ok=True stops it complaining when the folder is already there.
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return frame


def list_snapshots() -> pd.DataFrame:
    """Show which snapshots exist, how big they are and when they were made.

    Use this when a table looks stale and you are deciding what to refresh, or
    when data/research/ has grown and you want to know what is in it.

    Steps:
        1. Return an empty frame straight away if the snapshot folder has never
           been created.
        2. Walk every `.parquet` file in the folder and read its size and
           modification time from the filesystem.
        3. Build a small DataFrame from those rows, newest first.

    Returns:
        pd.DataFrame: One row per snapshot with columns `name` (what to pass to
            `snapshot`), `megabytes`, and `modified` (a pandas Timestamp). Empty
            with those same columns when nothing has been saved yet.
    """
    columns = ["name", "megabytes", "modified"]

    if not SNAPSHOT_DIR.exists():
        return pd.DataFrame(columns=columns)

    # One dict per file: stat() reads the filesystem's own record of the file.
    rows = [
        {
            "name": path.stem,
            "megabytes": round(path.stat().st_size / 1_000_000, 2),
            "modified": pd.Timestamp(path.stat().st_mtime, unit="s"),
        }
        for path in SNAPSHOT_DIR.glob("*.parquet")
    ]

    return pd.DataFrame(rows, columns=columns).sort_values("modified", ascending=False)


def clear_snapshots(name: str = None) -> list:
    """Delete one snapshot, or all of them, so they get rebuilt from source.

    The blunt alternative to passing `refresh=True` everywhere. Safe: every
    snapshot can be rebuilt by calling its dataset function again.

    Steps:
        1. If a name was given, delete just that file. If not, collect every
           `.parquet` file in the snapshot folder.
        2. Delete each one, ignoring any that is already gone.
        3. Return the names of what was actually deleted.

    Args:
        name: The single snapshot to delete, or None, the default, to delete
            every snapshot.

    Returns:
        list: The names deleted, as strings. Empty if there was nothing to
            delete.
    """
    if not SNAPSHOT_DIR.exists():
        return []

    targets = [snapshot_path(name)] if name else list(SNAPSHOT_DIR.glob("*.parquet"))

    deleted = []
    for path in targets:
        # missing_ok stops this raising when the file was already deleted.
        if path.exists():
            path.unlink(missing_ok=True)
            deleted.append(path.stem)

    return deleted

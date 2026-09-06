"""Builds the app's AppContext for a notebook, once per Python kernel.

`streamlit_state.py` does this job for the Streamlit app, using
`@st.cache_resource` so the expensive object is built only once. A notebook
cannot use that decorator -- importing Streamlit outside a `streamlit run`
process prints warnings and buys nothing -- so this module does the same trick
with a plain module-level variable.

The result is that `get_context()` is slow the first time you call it in a
kernel and instant every time after, for as long as that kernel lives.
"""

import os
from pathlib import Path

from app_context import AppContext

PROJECT_ROOT = Path(__file__).resolve().parent.parent
"""The DataHuddle repo folder. Everything else in `research/` locates files
relative to this rather than to the working directory, because a notebook's
working directory is whatever folder the notebook file sits in."""

SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]
"""Seasons of season-long game logs to load. Deliberately a copy of the list in
streamlit_state.py rather than an import of it: importing that module would
import Streamlit, and research code has no business needing it. If you change
one, change the other."""

DFS_SEASONS = [2023, 2024, 2025]
"""Seasons the Daily Fantasy data covers. Shorter than SEASONS on purpose --
this data is play-by-play, which is 372 MB per season before trimming. Same
copy-not-import reasoning as above."""

# Holds the one context once it has been built. None means "not built yet".
_context = None


def project_root() -> Path:
    """Get the path to the DataHuddle repo folder.

    Useful in a notebook whenever you want to read or write a file at a fixed
    place in the project (a CSV in data/, a saved model) without caring which
    folder the notebook itself lives in.

    Steps:
        1. Hand back the `PROJECT_ROOT` constant above, which was worked out
           from this file's own location at import time.

    Returns:
        Path: The absolute path to the repo folder, e.g.
            `/Users/you/Desktop/DataHuddle`.
    """
    return PROJECT_ROOT


def get_context(seasons: list = None, dfs_seasons: list = None, rebuild: bool = False):
    """Get the shared AppContext, building it on the first call only.

    This is the doorway from a notebook into the app. The object it returns
    carries every repository, adapter and service the Streamlit pages use, so
    anything a page can show, a notebook can compute.

    Steps:
        1. If a context already exists and `rebuild` is False, return it
           unchanged. This is what makes repeat calls free.
        2. Otherwise build a fresh `AppContext` (from app_context.py, the app's
           composition root) with the season lists above.
        3. Store it in the module-level `_context` so the next call skips
           step 2, then return it.

    Args:
        seasons: Which seasons of season-long game logs to make available, as a
            list of years. Leave it out to use `SEASONS` above.
        dfs_seasons: Which seasons the Daily Fantasy data covers. Leave it out
            to use `DFS_SEASONS` above. Keep this short -- each season is a
            large play-by-play download.
        rebuild: Pass True to throw away the existing context and build a new
            one. Needed only when you have changed the season lists mid-kernel;
            an ordinary code change is picked up by `%autoreload` instead.

    Returns:
        AppContext: The shared context. Reach services on it by attribute, e.g.
            `ctx.projections_service`, `ctx.adp_comparison_service`,
            `ctx.dfs_read_repo`.

    Note:
        Building this touches neither the network nor MongoDB -- every
        repository loads lazily. The wait happens on the first call that
        actually asks for data, which is why `datasets.py` caches those results
        to disk.
    """
    global _context

    if _context is not None and not rebuild:
        return _context

    # MongoDB backs the projections, ADP and rankings collections. nflverse
    # data (game logs, play-by-play) works without it, so this is a warning
    # rather than an error -- plenty of modeling never touches Mongo.
    if "MONGODB_URI" not in os.environ:
        print("Warning: MONGODB_URI is not set, so anything reading MongoDB "
              "(projections, ADP, rankings) will fail. nflverse data still "
              "works. Set it in ~/.zshrc and restart the kernel.")

    _context = AppContext(seasons or SEASONS, dfs_seasons=dfs_seasons or DFS_SEASONS)
    return _context

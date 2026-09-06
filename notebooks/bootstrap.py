"""Makes `import research` work from inside a notebook. Import this first.

THE PROBLEM: Python only imports from folders it knows about, and a Jupyter
kernel starts out knowing about the folder its notebook lives in -- this one --
not the repo folder above it. So `from research import ...` fails in a fresh
notebook with `ModuleNotFoundError`, and so would `from services import ...`.

THE FIX: this file sits NEXT TO the notebooks, so importing it always works.
Importing it adds the repo folder to Python's search path, after which every
package in the project imports normally. It then re-exports the research
helpers, so one line in your first cell is enough:

    from bootstrap import get_context, dfs_player_weeks, snapshot

This is the same problem scripts/load_data.py solves with its own
`sys.path.insert`, and the same one pytest.ini solves with `pythonpath = .`.
"""

import sys
from pathlib import Path

# The repo folder: this file's folder (notebooks/) then one level up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Put it FIRST in the search path so the project's own modules win over any
# same-named package installed in the environment. The `not in` check keeps a
# re-import (which %autoreload does often) from stacking duplicate entries.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Now that the path is set, the project imports. Everything below is a
# convenience re-export -- `from research.datasets import ...` works just as
# well once this module has been imported.
from research import (  # noqa: E402  (import must follow the sys.path line above)
    adp_table,
    clear_snapshots,
    dfs_player_weeks,
    get_context,
    list_snapshots,
    player_game_logs,
    project_root,
    season_projections,
    snapshot,
    snapshot_path,
)

__all__ = [
    "PROJECT_ROOT",
    "adp_table",
    "clear_snapshots",
    "dfs_player_weeks",
    "get_context",
    "list_snapshots",
    "player_game_logs",
    "project_root",
    "season_projections",
    "snapshot",
    "snapshot_path",
]

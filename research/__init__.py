"""The modeling sandbox: everything notebooks need to get at the app's data.

This package exists so a Jupyter notebook can reach the same data the Streamlit
app shows WITHOUT importing Streamlit or re-implementing any of the joining,
player-id resolution and scoring that `services/` already does.

Three pieces, each in its own module:

- `context.py`   builds the app's AppContext once per kernel, so a notebook can
                 call any repository, adapter or service the app uses.
- `snapshots.py` saves a DataFrame to a Parquet file under data/research/ and
                 reads it back on later runs. This is what makes a kernel
                 restart cheap instead of a two-minute reload.
- `datasets.py`  the ready-made modeling tables — one function per table, each
                 already wrapped in a snapshot.

Nothing in here is imported by the app. It is a one-way dependency: research
reads the app, the app never reads research.

Typical first cell of a notebook:

    from bootstrap import dfs_player_weeks
    weeks = dfs_player_weeks()
"""

from research.context import get_context, project_root
from research.snapshots import snapshot, snapshot_path, clear_snapshots, list_snapshots
from research.datasets import (
    adp_table,
    dfs_player_weeks,
    player_game_logs,
    season_projections,
)

__all__ = [
    "get_context",
    "project_root",
    "snapshot",
    "snapshot_path",
    "clear_snapshots",
    "list_snapshots",
    "adp_table",
    "dfs_player_weeks",
    "player_game_logs",
    "season_projections",
]

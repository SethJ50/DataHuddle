# Notebooks — the modelling sandbox

Somewhere to build models against the app's data without touching the app. Nothing in
here is imported by Streamlit; the dependency runs one way only, so an experiment can
never break a page.

## Setup, once

```bash
pip install -r requirements-research.txt   # app deps + jupyter, sklearn, xgboost
jupyter lab                                # from the repo root
```

`MONGODB_URI` should already be exported in your `~/.zshrc` — it is the same variable the
app uses. Without it, nflverse data (game logs, play-by-play) still works; projections,
ADP and rankings do not.

## The three pieces

| What | Where | For |
|---|---|---|
| `bootstrap` | [bootstrap.py](bootstrap.py) | Import it first in every notebook. Makes the rest of the project importable. |
| Ready-made tables | [../research/datasets.py](../research/datasets.py) | `dfs_player_weeks()`, `player_game_logs()`, `season_projections()`, `adp_table()` |
| The app itself | [../research/context.py](../research/context.py) | `get_context()` → every repository and service the pages use |

Start from [00_starter.ipynb](00_starter.ipynb), which walks through all three plus a
worked baseline model.

## Snapshots

Every ready-made table is cached to a Parquet file under `data/research/` the first time
it is built. The first call to `dfs_player_weeks()` takes a couple of minutes; every call
after — including after a kernel restart — takes under a second.

```python
weeks = dfs_player_weeks()                # cached
weeks = dfs_player_weeks(refresh=True)    # rebuild from source, e.g. after a week of games

list_snapshots()      # what's cached, how big, how old
clear_snapshots()     # delete the lot; everything rebuilds on next use
```

Cache your own slow tables the same way:

```python
table = snapshot("my_table", lambda: expensive_thing())
```

The `lambda:` matters — without it the slow work runs before `snapshot` is even called.

`data/research/` is gitignored. So is `notebooks/artifacts/`, which is where a pickled
model should go if you save one.

## Conventions

- **Name notebooks with a number prefix** (`01_target_share_model.ipynb`) so they sort in
  the order you made them.
- **Split on time, not at random.** Train on earlier seasons, test on a later one. A random
  split lets the model see the future and flatters it badly.
- **Compare against a dumb baseline.** "He'll do what he did last week" is the bar. A model
  that can't clear it isn't a model.
- **Promote what proves out.** A table that more than one notebook wants belongs in
  `research/datasets.py`. Logic the app should eventually use belongs in `services/`, with
  tests — see how `draft_model/` grew.
- **Clear outputs before committing** (Kernel → Restart & Clear Output). Cell outputs make
  notebook diffs unreadable and can bloat the repo.

"""Downloads every nflverse file the public pages need, ahead of time.

Run once while the Docker image is being BUILT, not while the app is running.

The problem it solves: nflreadpy fetches its data from the internet the first
time somebody asks for it, and that first fetch costs about two minutes. On a
Hugging Face Space there is no disk that survives a restart -- persistent
storage was discontinued -- so without this, every single wake-up would make
whoever showed up first sit through that download.

The trick is that a Docker IMAGE does survive. Running this during the build
bakes the downloaded files into the image itself, so a restarted container
already has them.

    Cheap insurance, not a guarantee. nflreadpy treats a cached file as stale
    after 24 hours, so a container that wakes up two days later re-downloads
    anyway. This removes the wait in the common case; it does not abolish it.

Deliberately never fails. nflverse serves its files through GitHub, which
returns a 503 often enough to matter, and a warm-up that is only an
optimisation has no business breaking a deploy. Every error is caught,
reported, and swallowed -- a build that logs failures here still produces a
working image, just a slower one on first load.
"""

import sys
import traceback
from pathlib import Path

# Running `python scripts/warm_nflverse_cache.py` puts `scripts/` on the import
# path, not the project root -- so `import repositories` fails without this.
# Same two lines as scripts/run_draft_sim.py, for the same reason.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import nflreadpy as nfl

from repositories.nflreadpy_setup import configure
from streamlit_state import DFS_SEASONS, SEASONS


def loaders():
    """List every nflverse download the app makes, ready to be called.

    Read straight off the repositories so this list cannot drift: these are the
    exact `nfl.load_*` calls in `repositories/nfl_read_repo.py` and
    `repositories/dfs_read_repo.py`. The season lists come from
    `streamlit_state.py`, the same ones the running app uses, so warming and
    running can never disagree about which years to fetch.

    Steps:
        1. Pair a human-readable name with a zero-argument function for each
           download, so the caller can report which one failed without needing
           to know anything about nflreadpy.

    Returns:
        list: Pairs of `(name, function)`. Calling a function performs one
            download and returns a Polars DataFrame, which is thrown away --
            the point is the file left behind in the cache directory.
    """
    warm = [
        # Season-long side of the app. `SEASONS` is the longer list, because a
        # game log costs far less per season than play-by-play does.
        ("player stats", lambda: nfl.load_player_stats(SEASONS)),
        ("player directory", lambda: nfl.load_players()),
        ("teams", lambda: nfl.load_teams()),

        # Not season-scoped: one table mapping a player's id across every data
        # source. Slow -- measured at around two minutes -- which makes it the
        # single most worthwhile thing in this list to bake in.
        ("player id crosswalk", lambda: nfl.load_ff_playerids()),

        # Daily Fantasy side. `DFS_SEASONS` is deliberately shorter.
        ("play-by-play", lambda: nfl.load_pbp(DFS_SEASONS)),
        ("snap counts", lambda: nfl.load_snap_counts(DFS_SEASONS)),
        ("schedules", lambda: nfl.load_schedules(DFS_SEASONS)),
        ("team stats", lambda: nfl.load_team_stats(DFS_SEASONS)),

        # `stat_type="weekly"` matched to the call in dfs_read_repo.py. Warming
        # a different stat_type would download a file the app never asks for
        # and leave the one it does ask for cold.
        ("ff opportunity",
         lambda: nfl.load_ff_opportunity(DFS_SEASONS, stat_type="weekly")),
    ]

    # These two are fetched once per stat_type, and the app asks for several.
    # The lists come from TRACKING_COLUMNS and CHARTING_COLUMNS in
    # services/dfs_player_service.py, which is what decides how many separate
    # downloads the Cheat Sheet actually triggers.
    for kind in ("receiving", "rushing", "passing"):
        warm.append((f"nextgen stats ({kind})",
                     lambda k=kind: nfl.load_nextgen_stats(DFS_SEASONS,
                                                           stat_type=k)))

    for kind in ("rec", "rush"):
        warm.append((f"pfr advanced stats ({kind})",
                     lambda k=kind: nfl.load_pfr_advstats(DFS_SEASONS,
                                                          stat_type=k)))

    # `k=kind` above is not a typo. A plain `lambda: ...kind...` would capture
    # the VARIABLE, not its value, so every lambda in the loop would end up
    # using whatever `kind` was left holding after the last iteration. Binding
    # it as a default argument freezes the current value instead.
    return warm


def main():
    """Fetch everything in `loaders()`, reporting failures without raising.

    Pass `--list` to print what would be downloaded and stop. That costs
    nothing and touches no network, which makes it a quick way to see what a
    build is about to spend its time on -- and it is what
    `tests/test_deployment_config.py` runs to prove this file still works when
    executed the way the Dockerfile executes it.

    Steps:
        1. If `--list` was passed, print the planned downloads and stop.
        2. Otherwise call `configure` from `repositories/nflreadpy_setup.py`,
           which is what switches nflreadpy from its in-memory default to
           caching on disk. Without this the downloads would land nowhere and
           the whole exercise would be pointless.
        3. Work through `loaders()` above, calling each one and printing
           whether it worked.
        4. Print a one-line tally at the end.

    Returns:
        int: Always 0, so `docker build` treats this step as successful even
            when nflverse was having a bad day. The log is where you look to
            find out whether the image is actually warm.

    Note:
        Only DOWNLOAD failures are swallowed. An import error still crashes the
        build, on purpose -- that means the image is broken in a way a slow
        first page load would hide rather than reveal.
    """
    if "--list" in sys.argv:
        for name, _ in loaders():
            print(name)
        return 0

    configure()

    failed = []
    for name, load in loaders():
        try:
            load()
            print(f"  warmed: {name}", flush=True)
        except Exception:
            failed.append(name)
            print(f"  FAILED: {name}", flush=True)
            # Printed in full because a build log is the only place anyone will
            # ever see this, and "it was slow on first load" is a miserable
            # symptom to debug months later without the original traceback.
            traceback.print_exc()

    if failed:
        print(f"cache warm incomplete -- {len(failed)} of {len(loaders())} "
              f"failed: {', '.join(failed)}. The image still works; the first "
              f"page load will just download these itself.", flush=True)
    else:
        print("cache warm complete.", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())

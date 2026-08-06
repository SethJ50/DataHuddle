"""Adapter for the four UDK (Ultimate Draft Kit) position-rankings
collections — QB, RB, WR, TE — concatenated into one canonical DataFrame.

All four collections share an identical schema, so this adapter is a
straightforward rename + concat, unlike FfbProjectionsAdapter (which has to
untangle duplicate CSV headers). This is the source of truth for "which
players does the app consider in scope" — see services/roster_service.py.
"""

import pandas as pd


class UdkRankingsAdapter:
    """Combines UDK's four position ranking files into one player table.

    UDK publishes its rankings split by position, one file each for QB, RB, WR,
    and TE. The rest of the app wants a single list of players, and it wants the
    column names the app uses rather than UDK's. This class does both, and is
    the place that decides which players the app considers to exist at all.
    """

    def __init__(self, qb_collection_repo, rb_collection_repo, wr_collection_repo, te_collection_repo):
        """Remember where each of the four position rankings can be read from.

        The four data sources are handed in rather than looked up here, so tests
        can pass fake ones and the app can wire in the real database-backed ones.

        Steps:
            1. Store each of the four repository objects on the instance for
               `load` to use later. Nothing is read from the database yet.

        Args:
            qb_collection_repo: An object with a `.read()` method that returns
                the quarterback rankings as a DataFrame.
            rb_collection_repo: Same, for running backs.
            wr_collection_repo: Same, for wide receivers.
            te_collection_repo: Same, for tight ends.
        """
        self._qb_collection_repo = qb_collection_repo
        self._rb_collection_repo = rb_collection_repo
        self._wr_collection_repo = wr_collection_repo
        self._te_collection_repo = te_collection_repo

    def load(self) -> pd.DataFrame:
        """Read all four position rankings and return them as one renamed table.

        This is the only method callers need. It hides both the fact that the
        data arrives in four pieces and the fact that UDK's column names differ
        from the ones used everywhere else in the app.

        Steps:
            1. Call `.read()` on each of the four repositories, giving four
               DataFrames of UDK-shaped rankings.
            2. Stack them on top of each other with `pd.concat`.
               `ignore_index=True` renumbers the rows 0, 1, 2, ... so the four
               sources do not repeat each other's row numbers.
            3. Build a new DataFrame that copies each UDK column across under
               the app's own lowercase name.

        Returns:
            pd.DataFrame: One row per ranked player, with columns `name`,
                `position`, `team`, `bye_week`, `rank`, `points`, `risk`,
                `upside`, `adp`, and `tier`. Note that `rank` is UDK's rank
                *within that player's position*, not overall, because it came
                from a per-position file.

        Raises:
            KeyError: If a source file is missing one of UDK's expected column
                names, which usually means the export format changed.
        """
        # Four DataFrames, one per position, all sharing UDK's column names:
        # Name, Position, Team, Bye Week, Rank, Points, Risk, Upside, ADP, Tier.
        frames = [
            self._qb_collection_repo.read(),
            self._rb_collection_repo.read(),
            self._wr_collection_repo.read(),
            self._te_collection_repo.read(),
        ]
        combined = pd.concat(frames, ignore_index=True, sort=False)

        return pd.DataFrame({
            "name": combined["Name"],
            "position": combined["Position"],
            "team": combined["Team"],
            "bye_week": combined["Bye Week"],
            "rank": combined["Rank"],
            "points": combined["Points"],
            "risk": combined["Risk"],
            "upside": combined["Upside"],
            "adp": combined["ADP"],
            "tier": combined["Tier"],
        })

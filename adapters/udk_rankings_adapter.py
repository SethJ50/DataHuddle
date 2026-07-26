"""Adapter for the four UDK (Ultimate Draft Kit) position-rankings
collections — QB, RB, WR, TE — concatenated into one canonical DataFrame.

All four collections share an identical schema, so this adapter is a
straightforward rename + concat, unlike FfbProjectionsAdapter (which has to
untangle duplicate CSV headers). This is the source of truth for "which
players does the app consider in scope" — see services/roster_service.py.
"""

import pandas as pd


class UdkRankingsAdapter:
    def __init__(self, qb_collection_repo, rb_collection_repo, wr_collection_repo, te_collection_repo):
        self._qb_collection_repo = qb_collection_repo
        self._rb_collection_repo = rb_collection_repo
        self._wr_collection_repo = wr_collection_repo
        self._te_collection_repo = te_collection_repo

    def load(self) -> pd.DataFrame:
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
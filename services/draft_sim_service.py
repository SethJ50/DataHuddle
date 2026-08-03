"""Wires the draft model to the app's data and to saved simulation runs.

draft_model/ is deliberately free of Mongo and Streamlit so it can be tested in
isolation. This is the seam where it meets the rest of the app: it pulls FFC ADP,
platform ADP and projections out of the service layer, assembles the model table,
loads the matching simulation artifact, and hands back something a page can
render without knowing anything about picks matrices.

Deliberately NOT importing streamlit -- caching belongs to the page, which is
where the existing app puts it. That keeps this class testable and reusable from
scripts/run_draft_sim.py, which shares the same table-building code so the two
cannot drift apart.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from draft_model.artifacts import artifact_path, load_picks_matrix, matches_table
from draft_model.config import PLATFORM_WEIGHT, DraftConfig
from draft_model.queries import (
    availability_matrix, compute_vorp, cost_of_waiting, expected_best_at_pick,
    positional_cost_of_waiting, prob_any_available, replacement_value,
)
from draft_model.table import blend_adp, build_table

# Weighted toward the platform the league actually drafts on -- the default list
# a platform shows in-app anchors real leaguemates far more than consensus does.
BASE_PLATFORM_WEIGHTS = {"espn": 0.25, "yahoo": 0.25, "sleeper": 0.25}
DRAFTING_PLATFORM_WEIGHT = 0.5


@dataclass
class DraftBoard:
    """One draft's model table joined to its simulation results.

    Everything a page needs, already aligned: `table` row i describes
    `artifact.picks[:, i]`, and `vorp[i]` is that same player's value over
    replacement. Keeping them together is what stops the alignment invariant
    from having to be re-established at every call site.

    Attributes:
        config: The league this was built for.
        table: One row per player, in picks-matrix column order.
        artifact: The loaded simulation.
        vorp: Value over replacement per player; NaN where undefined.
        replacement: Position -> replacement-level points.
        stale: True if the artifact's player ordering no longer matches the
            table -- meaning the data changed since the simulation was run.
    """

    config: DraftConfig
    table: pd.DataFrame
    artifact: object
    vorp: np.ndarray
    replacement: dict
    stale: bool

    @property
    def calibrated(self) -> bool:
        return bool(self.artifact.metadata.get("calibrated", False))

    @property
    def n_sims(self) -> int:
        return self.artifact.n_sims

    def availability(self, target_picks=None) -> pd.DataFrame:
        """
        Purpose: One row per player with availability at each of your picks, plus
            the value context needed to act on it.

        Parameters:
            target_picks (sequence[int] | None): Defaults to config.my_picks.

        Returns:
            pd.DataFrame in ADP order with columns: name, position, team,
            adp_target, projection, vorp, cost_of_waiting, and one `P@<pick>`
            column per target pick holding a probability in [0, 1].

        Notes:
            cost_of_waiting is computed against your NEXT pick specifically --
            the decision is always "take him now or risk losing him before I
            pick again", so the relevant horizon is one turn, not the whole draft.
        """
        picks_to_use = list(target_picks or self.config.my_picks)
        grid = availability_matrix(self.artifact.picks, picks_to_use)

        # canonical_id rides along so the UI can join this to its own player
        # tables, which are keyed by nflreadpy id rather than FFC's. It is
        # nullable -- team defenses never resolve -- so joins must tolerate NaN.
        frame = self.table[["canonical_id", "name", "position", "team", "adp_target"]].copy()
        frame["projection"] = self.table.get("projection", np.nan)
        frame["vorp"] = self.vorp

        # "Your next pick" is the first one you own that is still ahead of the
        # player's typical draft slot; falling back to the second pick keeps this
        # meaningful pre-draft, when every pick is still ahead of you.
        next_pick = picks_to_use[1] if len(picks_to_use) > 1 else picks_to_use[0]
        positions = self.table["position"].to_numpy()
        frame["cost_of_waiting"] = [
            cost_of_waiting(self.artifact.picks, i, next_pick, self.vorp, positions)
            for i in range(len(self.table))
        ]

        for column, pick in enumerate(picks_to_use):
            frame[f"P@{pick}"] = grid[:, column]

        return frame

    def positional_costs(self, at_pick=None, next_pick=None) -> pd.DataFrame:
        """
        Purpose: For each position, what it costs to wait a round before addressing it.

        Parameters:
            at_pick (int | None): The pick being made now. Defaults to your first.
            next_pick (int | None): Your following pick. Defaults to your second.

        Returns:
            pd.DataFrame with columns position, best_available_vorp, cost, sorted
            by cost descending -- so the position most urgent to address is first.

        Notes:
            THIS is the number that chooses between positions, and it is not a
            sum of the per-player costs. A deep, interchangeable tier scores near
            zero however likely each individual is to be gone; one elite player
            with a cliff behind him scores high even at modest odds.
        """
        my_picks = list(self.config.my_picks)
        at_pick = at_pick if at_pick is not None else my_picks[0]
        next_pick = next_pick if next_pick is not None else (
            my_picks[1] if len(my_picks) > 1 else at_pick
        )

        positions = self.table["position"].to_numpy()
        rows = []
        for position in sorted(set(positions)):
            at_position = (positions == position) & np.isfinite(self.vorp)
            if not at_position.any():
                continue
            rows.append({
                "position": position,
                "best_available_vorp": expected_best_at_pick(
                    self.artifact.picks, np.flatnonzero(at_position),
                    self.vorp[at_position], at_pick,
                ),
                "cost": positional_cost_of_waiting(
                    self.artifact.picks, position, at_pick, next_pick,
                    self.vorp, positions,
                ),
            })

        return pd.DataFrame(rows).sort_values("cost", ascending=False).reset_index(drop=True)

    def tier_survival(self, player_names, target_pick) -> float:
        """
        Purpose: Probability at least one of a chosen group lasts to a pick.

        Parameters:
            player_names (sequence[str]): Names as they appear in the table.
            target_pick (int): The pick to evaluate at.

        Returns:
            float in [0, 1]. 0.0 if none of the names are in the pool.

        Notes:
            A genuinely different question from the individual percentages, and
            the one that decides whether to wait. Treating the players as
            independent understates it, because they compete for the same picks --
            one going early is what lets another survive.
        """
        wanted = self.table.index[self.table["name"].isin(list(player_names))].tolist()
        if not wanted:
            return 0.0
        return prob_any_available(self.artifact.picks, wanted, target_pick)


class DraftSimService:
    """Builds model tables and serves saved simulation results to the UI."""

    def __init__(self, ffc_service, adp_comparison_service, projections_service,
                 sim_dir="data/sim"):
        self._ffc_service = ffc_service
        self._adp_comparison_service = adp_comparison_service
        self._projections_service = projections_service
        self._sim_dir = Path(sim_dir)

    def platform_blend(self, fmt, platform) -> pd.Series:
        """
        Purpose: One consensus ADP per player from ESPN, Yahoo and Sleeper.

        Parameters:
            fmt (ScoringFormat): Which format's ADP to read.
            platform (str): The platform this league drafts on; weighted up.

        Returns:
            pd.Series: Blended ADP indexed by canonical_id.
        """
        comparison = self._adp_comparison_service.compare(fmt).set_index("canonical_id")
        sources = {
            "espn": comparison["espn_adp"].dropna(),
            "yahoo": comparison["yahoo_adp"].dropna(),
            "sleeper": comparison["sleeper_adp"].dropna(),
        }
        weights = dict(BASE_PLATFORM_WEIGHTS)
        if platform in weights:
            weights[platform] = DRAFTING_PLATFORM_WEIGHT
        return blend_adp(sources, weights)

    def build_model_table(self, config) -> pd.DataFrame:
        """
        Purpose: Assemble the table the simulation runs on, from live app data.

        Parameters:
            config (DraftConfig): League settings, including the drafting
                platform. Taken from the config rather than passed separately so
                the platform used to BUILD a table can never disagree with the
                one baked into the artifact's fingerprint.

        Returns:
            pd.DataFrame -- build_table's output: one row per player in ADP order
            with ffc_player_id, canonical_id (nullable), name, position, team,
            adp_target, stdev_target, times_drafted, mu, sd, projection.

        Raises:
            ValueError: If no FFC data is loaded for this scoring format.

        Notes:
            Shared with scripts/run_draft_sim.py deliberately. If the script and
            the app built their tables separately they would eventually disagree,
            and the app would be reading a matrix whose columns mean something
            slightly different from what it thinks.
        """
        ffc = self._ffc_service.with_canonical_id(config.scoring_format)
        if ffc.empty:
            raise ValueError(
                f"no FFC data loaded for {config.scoring_format.value}. "
                f"Run scripts/load_data.py first."
            )

        projections = self._projections_service.get_own_projections()
        points_column = f"fantasy_points_{config.scoring_format.value}_season"

        return build_table(
            config, ffc,
            platform_adp=self.platform_blend(config.scoring_format, config.platform),
            enrichments={
                "projection": projections.set_index("canonical_id")[points_column]
            },
            platform_weight=PLATFORM_WEIGHT,
        )

    def artifact_for(self, draft_id, config) -> Path:
        """Where this draft's simulation lives, given its current settings."""
        return artifact_path(self._sim_dir, draft_id, config)

    def has_simulation(self, draft_id, config) -> bool:
        """Whether a simulation exists for these exact settings."""
        return self.artifact_for(draft_id, config).exists()

    def board_signature(self, draft_doc, year) -> str:
        """
        Purpose: A short string that changes whenever `load_board` would return
            something different. The UI uses it as a cache key.

        Parameters:
            draft_doc (dict): A draft from DraftService.
            year (int): Season.

        Returns:
            str: An opaque key, e.g. "e618ec19a24f|4|[('QB', 1), ...]|None|17..."
            Its content is meaningless; only whether it CHANGED matters.

        Notes:
            WHY THIS EXISTS. Streamlit caches the loaded board so the big picks
            matrix isn't re-read on every click. Cache it under the draft_id
            alone and editing that draft's settings does not change the key --
            so the page keeps serving the board it built for the OLD settings,
            even after the artifact for the new ones has been simulated and
            saved. That is a silently wrong answer, which is the one failure
            mode this whole module is built to avoid.

            The key deliberately covers MORE than config.fingerprint() does. The
            fingerprint answers "would the simulation differ?"; this answers
            "would the loaded board differ?", which is a broader question:

              fingerprint      -- teams, rounds, format, platform, keepers, seed
              draft_position   -- excluded from the fingerprint on purpose (it
                                  picks which columns you LOOK at, not how the
                                  draft unfolds) but it absolutely changes the
                                  board's my_picks
              starting_slots,
              roster_size      -- likewise recomputed at load time, so they
                                  change the board without changing the artifact
              artifact mtime   -- so re-running the sim under UNCHANGED settings,
                                  which overwrites the same filename, still
                                  refreshes the page
        """
        config = DraftConfig.from_draft_doc(draft_doc, year=year)
        path = self.artifact_for(draft_doc["draft_id"], config)

        # 0 when nothing has been simulated yet, so "missing" and "just written"
        # are different keys -- otherwise the page would go on showing the
        # no-simulation warning after you ran the script.
        mtime = path.stat().st_mtime_ns if path.exists() else 0

        return "|".join(str(part) for part in (
            config.fingerprint(),
            config.draft_position,
            sorted((config.starting_slots or {}).items()),
            config.roster_size,
            mtime,
        ))

    def load_board(self, draft_doc, year) -> DraftBoard:
        """
        Purpose: Everything the Draft Plan page needs for one draft.

        Parameters:
            draft_doc (dict): A draft from DraftService.
            year (int): Season.

        Returns:
            DraftBoard, with table, artifact, VORP and replacement level aligned.

        Raises:
            FileNotFoundError: No simulation for these settings. The message names
                the command to run, since that's the only fix.

        Notes:
            Changing any league setting changes the config fingerprint, so this
            raises rather than silently serving a simulation computed under
            different assumptions.

            `stale` is a separate, weaker signal: the settings match but the DATA
            has moved since the run (a newer FFC pull re-sorted the pool). The
            fingerprint cannot detect that, so it's surfaced for the page to warn
            about rather than treated as fatal.
        """
        config = DraftConfig.from_draft_doc(draft_doc, year=year)
        path = self.artifact_for(draft_doc["draft_id"], config)

        if not path.exists():
            raise FileNotFoundError(
                f"no simulation for '{draft_doc['name']}' with its current settings.\n"
                f"Run:  python scripts/run_draft_sim.py --draft-id {draft_doc['draft_id']}"
            )

        table = self.build_model_table(config)
        artifact = load_picks_matrix(path)

        projections = table.get("projection", pd.Series(np.nan, index=table.index)).to_numpy()
        positions = table["position"].to_numpy()
        replacement = replacement_value(
            projections, positions, config.starting_slots, config.num_teams
        )

        return DraftBoard(
            config=config,
            table=table,
            artifact=artifact,
            vorp=compute_vorp(projections, positions, replacement),
            replacement=replacement,
            stale=not matches_table(artifact, table),
        )

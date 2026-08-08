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
from functools import cached_property

import numpy as np
import pandas as pd

from draft_model.artifacts import artifact_path, load_picks_matrix, matches_table
from draft_model.config import PLATFORM_WEIGHT, DraftConfig
from draft_model.queries import (
    availability_matrix, best_available_by_position, compute_vorp,
    expected_best_at_pick, positional_cost_of_waiting, prob_any_available,
    replacement_value,
)
from draft_model.table import blend_adp, build_table
from draft_model.calibrate import simulated_mean_pick

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
        vorp: Value over replacement per player; NaN where undefined. A kept
            player's VORP is deliberately NaN — see the note below.
        replacement: Position -> replacement-level points.
        stale: True if the artifact's player ordering no longer matches the
            table -- meaning the data changed since the simulation was run.
        kept: One flag per player, True where another team is keeping him and so
            he can never be drafted by anybody.

    Note:
        SETTING A KEPT PLAYER'S VORP TO NaN IS LOad-BEARING, not cosmetic. Every
        value query in draft_model/queries.py already filters its candidates on
        `np.isfinite(vorp)`, so a NaN removes kept players from replacement
        comparisons, tier pricing and cost-of-waiting without those functions
        needing to learn what a keeper is.

        Availability is the exception and has to be handled explicitly, because
        it is pure pick arithmetic with no notion of value: a keeper carries his
        keeper pick in the matrix, so `pick >= target` would report him as
        available right up until his team spends that pick on him. `availability`
        below zeroes those rows.
    """

    config: DraftConfig
    table: pd.DataFrame
    artifact: object
    vorp: np.ndarray
    replacement: dict
    stale: bool
    kept: np.ndarray = None

    @property
    def calibrated(self) -> bool:
        """Report whether this simulation was calibrated before it was saved.

        An uncalibrated run still produces numbers, but its availability
        probabilities describe a subtly different league than yours, so the UI
        warns when this is False.

        Steps:
            1. Look for a "calibrated" flag in the artifact's metadata, treating
               an older artifact that predates the flag as not calibrated.

        Returns:
            bool: True if the saved run recorded that it was calibrated.
        """
        return bool(self.artifact.metadata.get("calibrated", False))

    @property
    def n_sims(self) -> int:
        """Report how many drafts were simulated for this board.

        Shown in the UI as a confidence cue: a probability from 10,000 drafts is
        worth more than one from 500.

        Steps:
            1. Pass through to the artifact's own `n_sims` property.

        Returns:
            int: The number of simulated drafts behind these numbers.
        """
        return self.artifact.n_sims

    @cached_property
    def simulated_adp(self):
        """Work out the average pick each player actually went at in the simulation.

        The simulation's OWN version of ADP, as opposed to the vendor ADP it was
        told to reproduce. Calibration's entire job is to make those two agree, so
        showing them side by side says how well it managed — and where the model
        genuinely disagrees with the market.

        `cached_property` means this is computed the FIRST time it is read and
        remembered on the board afterwards. That matters here: the picks matrix
        runs to a few million numbers, and the page reading this re-runs on every
        keystroke.

        Steps:
            1. Call `simulated_mean_pick` from draft_model/calibrate.py, which
               averages each player's column across the simulations while
               ignoring the drafts he went untaken in.
            2. Blank out the kept players with `kept_mask` above. A keeper's
               column holds the pick his team spends on him, which is a roster
               fact rather than a draft position — averaging it would print a
               confident-looking number that means nothing.

        Returns:
            np.ndarray: One average pick number per player, in table row order
                (so entry i describes `table.iloc[i]`, the same alignment
                everything else on this board keeps). NaN for a kept player, and
                for anyone drafted in no simulation at all.

        Note:
            Deliberately the SAME function calibration uses, rather than a
            re-derivation. Both are "mean pick, conditional on being drafted",
            which is also how vendors define ADP — if this column used a
            different definition it would show a gap that is really just two
            ways of counting.
        """
        mean_pick = simulated_mean_pick(self.artifact.picks)
        mean_pick[self.kept_mask()] = np.nan
        return mean_pick

    def availability(self, target_picks=None) -> pd.DataFrame:
        """Build the main table: who survives to each of your picks, and at what cost.

        The centerpiece of the Draft Plan page. Availability alone is not
        actionable — a 30% chance of losing someone barely better than his
        replacement does not matter — so the value context comes along with it.

        Steps:
            1. Settle which picks to evaluate at, defaulting to the ones you own.
            2. Call `availability_matrix` from draft_model/queries.py for the
               whole grid of probabilities in one pass.
            3. Start the output from the table's identifying columns, copying so
               the model table is not modified.
            4. Attach the projection and the precomputed VORP.
            5. Work out "your next pick", the horizon the waiting cost is measured
               against.
            6. Work out, per POSITION, the best value expected to survive to that
               pick, then subtract it from each player's own value.
            7. Add one probability column per target pick, named `P@<pick>`.

        Args:
            target_picks: Which pick numbers to evaluate availability at.
                Defaults to `config.my_selectable_picks` — the picks you own
                MINUS any spent on your own keeper, since a pick already spent is
                not one you can plan a selection around.

        Returns:
            pd.DataFrame: In ADP order, with columns `canonical_id`, `name`,
                `position`, `team`, `adp_target`, `projection`, `vorp`, `kept`,
                `cost_of_waiting`, and one `P@<pick>` column per target pick
                holding a probability between 0 and 1. A kept player has `kept`
                True, zero in every probability column, and a BLANK waiting cost
                — there is no decision to make about a player nobody can have.
                `canonical_id` is nullable — team defenses never resolve — so
                joins onto it must tolerate blanks.

        Note:
            cost_of_waiting is measured against your NEXT pick specifically --
            the decision is always "take him now or risk losing him before I
            pick again", so the relevant horizon is one turn, not the whole draft.

            It is a SIGNED gap and is not floored at zero. A negative value says
            the position should offer something better than this player next
            round, so the pick is better spent elsewhere -- which is real
            information. An earlier version multiplied the gap by the chance he
            was gone and then clamped it at zero, which drove roughly 89% of the
            column to exactly 0.0 and made it useless.
        """
        picks_to_use = list(target_picks or self.config.my_selectable_picks)
        grid = availability_matrix(self.artifact.picks, picks_to_use)

        # A kept player is on somebody's roster before the draft opens, so his
        # availability is zero at every pick regardless of what the matrix says.
        # The matrix records him at the pick his team spends on him, which reads
        # as "still there" for every earlier pick -- true of the pick number, but
        # not of anything you could act on.
        kept = self.kept_mask()
        grid[kept, :] = 0.0

        # canonical_id rides along so the UI can join this to its own player
        # tables, which are keyed by nflreadpy id rather than FFC's. It is
        # nullable -- team defenses never resolve -- so joins must tolerate NaN.
        frame = self.table[["canonical_id", "name", "position", "team", "adp_target"]].copy()
        frame["projection"] = self.table.get("projection", np.nan)
        frame["vorp"] = self.vorp
        frame["simulated_adp"] = self.simulated_adp
        frame["kept"] = kept


        # "Your next pick" is the first one you own that is still ahead of the
        # player's typical draft slot; falling back to the second pick keeps this
        # meaningful pre-draft, when every pick is still ahead of you.
        next_pick = picks_to_use[1] if len(picks_to_use) > 1 else picks_to_use[0]
        positions = self.table["position"].to_numpy()

        # How much better a player is than the best you could expect at his
        # position if you waited a round. Worked out once per POSITION and then
        # subtracted, rather than once per player -- the baseline does not depend
        # on which player at that position you are looking at.
        #
        # Kept players are excluded from the baseline: nobody can have them, so
        # they are not what you would fall back to. Their own cost comes out as
        # NaN and renders blank, which is honest -- there is no decision to make
        # about a player who was never available.
        fallback = best_available_by_position(
            self.artifact.picks, next_pick, self.vorp, positions,
            available_mask=~kept,
        )
        frame["cost_of_waiting"] = frame["vorp"] - frame["position"].map(fallback)

        for column, pick in enumerate(picks_to_use):
            frame[f"P@{pick}"] = grid[:, column]

        return frame

    def kept_mask(self) -> np.ndarray:
        """Work out which rows of the table are players somebody is keeping.

        Built from the config rather than stored on the table, so it stays right
        even if the table is rebuilt from newer data. Used to zero out
        availability and to mark kept players in the UI.

        Steps:
            1. If the board was given a `kept` array when it was built, use it.
            2. Otherwise derive one by testing each row's canonical id against
               the config's set of kept players.

        Returns:
            np.ndarray: One boolean per table row, True where that player is
                kept by some team. All False in a redraft league.
        """
        if self.kept is not None:
            return self.kept
        if "canonical_id" not in self.table.columns:
            return np.zeros(len(self.table), dtype=bool)
        return self.table["canonical_id"].isin(self.config.kept_player_ids).to_numpy()

    def positional_costs(self, at_pick=None, next_pick=None) -> pd.DataFrame:
        """Work out, for each position, what waiting a round costs you.

        Answers "which position should I address with this pick?" — a different
        question from "which player?", and one the per-player numbers cannot
        answer on their own.

        Steps:
            1. Settle the two picks being compared, defaulting to your first and
               second.
            2. Walk the positions present in the table, in alphabetical order.
            3. Skip any position where no player has a usable VORP, such as
               kickers and defenses.
            4. Call `expected_best_at_pick` from draft_model/queries.py for what
               the best player at that position is worth right now.
            5. Call `positional_cost_of_waiting` for the drop between now and your
               next pick.
            6. Sort by cost descending, so the most urgent position is first.

        Args:
            at_pick: The pick being made now. Defaults to your first.
            next_pick: Your following pick. Defaults to your second, or to
                `at_pick` if you only own one.

        Returns:
            pd.DataFrame: Columns `position`, `best_available_vorp`, and `cost`,
                sorted so the position most urgent to address comes first.

        Note:
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
        """Work out the chance at least one player from a chosen group survives.

        The "can I wait on this tier?" question. Pick out the players you would
        be happy with and ask whether any of them is likely to still be there.

        Steps:
            1. Find the table rows whose name appears in the requested group.
               Those row numbers are also the picks-matrix column indices, which
               is the alignment the DraftBoard exists to guarantee.
            2. Drop any kept players from that group. Leaving one in would make
               the answer certain for a player nobody can have.
            3. If none of the names matched, return 0.0 rather than raising.
            4. Hand the columns to `prob_any_available` from
               draft_model/queries.py.

        Args:
            player_names: The players to consider, named exactly as they appear
                in the table's `name` column.
            target_pick: The pick number to evaluate at.

        Returns:
            float: A probability between 0 and 1 that at least one of them lasts
                to that pick. 0.0 if none of the names are in the pool, so an
                answer of 0.0 can mean "none will last", "I did not recognize
                those names", or "every one of them is already kept".

        Note:
            A genuinely different question from the individual percentages, and
            the one that decides whether to wait. Treating the players as
            independent understates it, because they compete for the same picks --
            one going early is what lets another survive.
        """
        kept = self.kept_mask()
        wanted = [
            row for row in self.table.index[self.table["name"].isin(list(player_names))]
            if not kept[row]
        ]
        if not wanted:
            return 0.0
        return prob_any_available(self.artifact.picks, wanted, target_pick)


class DraftSimService:
    """Builds model tables and serves saved simulation results to the UI.

    The seam between the pure draft model and the rest of the app. It assembles
    the model's input table from live app data, finds the saved simulation
    matching a league's settings, and returns a DraftBoard the page can render
    without knowing anything about picks matrices.

    Note that it never RUNS a simulation. That is scripts/run_draft_sim.py's job,
    because a full run takes far too long for a page load.
    """

    def __init__(self, ffc_service, adp_comparison_service, projections_service,
                 sim_dir="data/sim"):
        """Store the three data services and remember where simulations are kept.

        Steps:
            1. Save each service on the instance.
            2. Convert the simulation directory to a Path, so path handling works
               the same on every operating system.

        Args:
            ffc_service: An `FfcService` supplying FFC ADP with canonical ids.
            adp_comparison_service: Supplies each platform's ADP per player.
            projections_service: Supplies your own projected fantasy points.
            sim_dir: The folder holding saved simulation files. The default is
                where run_draft_sim.py writes them.
        """
        self._ffc_service = ffc_service
        self._adp_comparison_service = adp_comparison_service
        self._projections_service = projections_service
        self._sim_dir = Path(sim_dir)

    def platform_blend(self, fmt, platform) -> pd.Series:
        """Blend ESPN, Yahoo, and Sleeper ADP into one number per player.

        The platform your league actually drafts on is weighted double the
        others, because the default list a platform shows in-app anchors your
        real leaguemates far more than any consensus ranking does.

        Steps:
            1. Load the comparison table and label its rows by canonical id.
            2. Split it into one ADP series per platform, dropping the players
               that platform has no number for.
            3. Start from the equal base weights and double the drafting
               platform's, if it is one of the three.
            4. Hand both to `blend_adp` from draft_model/table.py, which
               renormalizes per player so someone only one platform ranks still
               gets that platform's number rather than a fraction of it.

        Args:
            fmt: Which scoring format's ADP to read.
            platform: The platform this league drafts on, which gets the heavier
                weight. An unrecognized name simply leaves the weights equal.

        Returns:
            pd.Series: One blended ADP per player, labelled by canonical id. NaN
                for a player no platform ranks.
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
        """Assemble the table the simulation runs on, from live app data.

        Gathers FFC ADP, blended platform ADP, and your own projections, and
        hands them to the model's table builder. This is where app data becomes
        model input.

        Steps:
            1. Load FFC ADP with canonical ids attached, for this scoring format.
            2. Fail with a clear message if nothing is loaded, since an empty
               pool would otherwise produce a simulation of nothing.
            3. Load your own projections and work out which points column matches
               the scoring format.
            4. Call `build_table` from draft_model/table.py, passing the platform
               blend from `platform_blend` above as the centre adjustment and the
               projections as an enrichment column.

        Args:
            config: League settings, including the drafting platform. The
                platform is taken from the config rather than passed separately,
                so the platform used to BUILD a table can never disagree with the
                one baked into the artifact's fingerprint.

        Returns:
            pd.DataFrame: `build_table`'s output — one row per player in ADP
                order with `ffc_player_id`, a nullable `canonical_id`, `name`,
                `position`, `team`, `adp_target`, `stdev_target`,
                `times_drafted`, `mu`, `sd`, and `projection`.

        Raises:
            ValueError: If no FFC data is loaded for this scoring format. The
                message names the script to run, since that is the only fix.

        Note:
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
        """Work out where this draft's simulation file lives, for these settings.

        The filename encodes the settings, so a settings change points at a
        different file rather than at a stale one.

        Steps:
            1. Call `artifact_path` from draft_model/artifacts.py with this
               service's simulation folder, the draft id, and the config.

        Args:
            draft_id: Which draft the simulation belongs to.
            config: The league settings, which supply the fingerprint in the
                filename.

        Returns:
            Path: Where the file would be. This only computes a path; the file
                may or may not exist.
        """
        return artifact_path(self._sim_dir, draft_id, config)

    def has_simulation(self, draft_id, config) -> bool:
        """Check whether a simulation has been run for these exact settings.

        Lets a page show a "run the simulation first" message instead of
        catching an error from `load_board` below.

        Steps:
            1. Call `artifact_for` above for the path these settings imply.
            2. Ask whether a file is actually there.

        Args:
            draft_id: Which draft to check.
            config: The league settings to check for.

        Returns:
            bool: True if a matching simulation file exists on disk.
        """
        return self.artifact_for(draft_id, config).exists()

    @staticmethod
    def keeper_columns(config, table) -> dict:
        """Translate each keeper into the matrix column the simulator works in.

        The config names keepers by canonical player id, because that is what the
        user picked and what survives a data refresh. The simulator knows nothing
        about ids and addresses players purely by their column in the picks
        matrix. This is the one place those two vocabularies meet.

        Steps:
            1. Return nothing at all for a redraft league, so the simulator's
               keeper path is skipped entirely.
            2. Build a lookup from canonical id to table row. Rows with no
               canonical id — team defenses — are dropped first, since they can
               never be matched by id anyway.
            3. For each keeper, look up his row and record it against the pick
               his team spends, taken from `config.keeper_picks`.
            4. Raise if a kept player is not in the table, rather than quietly
               dropping him — see Raises.

        Args:
            config: The league settings, supplying both the keepers and the pick
                each one consumes.
            table: The model table, whose row order IS the matrix column order.

        Returns:
            dict: Maps an overall pick number to the column index of the player
                kept there, ready to hand to `sim_batch`. Empty for a redraft
                league.

        Raises:
            ValueError: If a kept player is not in the model table. Dropping him
                silently would be far worse than failing: his pick would go to
                the best available player instead, so the simulated draft would
                hand one team an extra selection nobody in the real league gets.
        """
        if not config.keepers:
            return {}

        rows = table.dropna(subset=["canonical_id"])
        row_by_id = {cid: row for row, cid in zip(rows.index, rows["canonical_id"])}

        columns, missing = {}, []
        for pick, canonical_id in config.keeper_picks.items():
            row = row_by_id.get(canonical_id)
            if row is None:
                missing.append(canonical_id)
                continue
            columns[pick] = int(row)

        if missing:
            raise ValueError(
                f"{len(missing)} kept player(s) are not in the simulated pool: "
                f"{', '.join(map(str, missing))}. They may be beyond the pool cap, "
                f"or have no FFC ADP for this scoring format."
            )
        return columns

    def board_signature(self, draft_doc, year) -> str:
        """Build a cache key that changes whenever the loaded board would differ.

        Streamlit caches the loaded board so the big picks matrix is not re-read
        on every click, and this string is what that cache is keyed on. Get it
        wrong and the page serves a board built for settings you have since
        changed.

        Steps:
            1. Build the config from the stored draft, so this and `load_board`
               below always agree on what the settings are.
            2. Work out where the artifact would live via `artifact_for` above.
            3. Read the file's modification time in nanoseconds, or use 0 when
               nothing has been simulated yet.
            4. Join the fingerprint, the draft position, the sorted starting
               slots, the roster size, and that timestamp into one string.

        Args:
            draft_doc: A draft document from DraftService.
            year: The season.

        Returns:
            str: An opaque key, for example
                "e618ec19a24f|4|[('QB', 1), ...]|None|17...". Its content is
                meaningless; only whether it CHANGED between two calls matters.

        Note:
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
        """Load everything the Draft Plan page needs for one draft, already aligned.

        The main entry point of this service. It rebuilds the model table from
        current data, loads the saved simulation, computes value on top, and
        packages all of it so the alignment between them holds.

        Steps:
            1. Build the config from the stored draft and work out where its
               artifact would live.
            2. Fail with an actionable message if no simulation exists for these
               exact settings.
            3. Rebuild the model table with `build_model_table` above, from
               current app data.
            4. Load the saved simulation with `load_picks_matrix` from
               draft_model/artifacts.py.
            5. Compute replacement level for each position with
               `replacement_value` from draft_model/queries.py.
            6. Turn projections into VORP with `compute_vorp`, and check the
               artifact still lines up with the table using `matches_table`.

        Args:
            draft_doc: A draft document from DraftService.
            year: The season.

        Returns:
            DraftBoard: The table, artifact, VORP, and replacement level, all
                aligned to each other, plus a `stale` flag.

        Raises:
            FileNotFoundError: If no simulation exists for these settings. The
                message names the command to run, since that is the only fix.
            ValueError: From `build_model_table` if no FFC data is loaded.

        Note:
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

        # Replacement level is computed over the WHOLE pool, keepers included.
        # A kept player still occupies a starting slot on somebody's roster, so
        # he is part of the league-wide supply that decides what "freely
        # available" is worth. Excluding him would flatter every other player.
        replacement = replacement_value(
            projections, positions, config.starting_slots, config.num_teams
        )

        kept = (table["canonical_id"].isin(config.kept_player_ids).to_numpy()
                if config.keepers else np.zeros(len(table), dtype=bool))

        # Blanking a kept player's VORP is what removes him from every value
        # query at once -- see the note on DraftBoard. Done here rather than
        # inside compute_vorp so the model layer stays unaware of keepers.
        vorp = compute_vorp(projections, positions, replacement)
        vorp[kept] = np.nan

        return DraftBoard(
            config=config,
            table=table,
            artifact=artifact,
            vorp=vorp,
            replacement=replacement,
            stale=not matches_table(artifact, table),
            kept=kept,
        )

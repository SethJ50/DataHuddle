"""Tracks one draft in progress, pick by pick.

A draft is stored as nothing more than an ORDERED LIST OF PICKS. Everything else
-- who has been taken, how many running backs each team holds, whose turn it is
-- is worked out from that list whenever it is needed, and never stored.

That rule is the whole design. A stored copy of a derived thing can fall out of
sync with the thing it came from, and then two parts of the app quietly disagree
about the same draft. A list of picks cannot disagree with itself.

It also makes undo almost free: removing the last entry undoes the last pick, and
keeping the first N entries rewinds to pick N+1. There is no unwinding logic to
get wrong.

This module deliberately imports neither streamlit nor pymongo. It is plain
Python and pandas, so it can be tested without a browser or a database.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from draft_model.config import POSITIONS, DraftConfig
from draft_model.engine import (
    draw_boards_for_sims, monte_carlo_sim, position_index, sim_batch,
)
from draft_model.mechanics import picks_for_slot, snake_order
from draft_model.queries import (
    FLEX_POSITIONS, adjust_within_position, availability_matrix,
    best_available_by_position, lineup_slot_masks, positional_cost_of_waiting,
    roster_from_picks,
)
from services.draft_sim_service import DraftSimService

def clean_id(value):
    """Turn a possibly-missing id into either a clean string or None.

    Ids arrive from three places that disagree about what "missing" looks like:
    pandas uses NaN, MongoDB uses None, and a number needs turning into text
    before it can be compared. This flattens all of that to one convention.

    NaN is the dangerous one. `bool(float("nan"))` is **True**, so a plain
    `if canonical_id` check lets it straight through, and pandas' `isin` matches
    every NaN against every other -- so one missing id looks equal to all the
    others.

    Steps:
        1. Pass None straight through.
        2. Ask pandas whether the value is missing, guarding the call since it
           raises on some types rather than answering.
        3. Convert to trimmed text, turning an empty string into None.

    Args:
        value: An id from anywhere -- a string, a number, None, or NaN.

    Returns:
        str | None: The id as text, or None when there genuinely isn't one.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


@dataclass
class DraftState:

    config: DraftConfig
    picks: list = field(default_factory=list)
    seed: int = 0

    @property
    def current_pick(self) -> int:
        """Work out which overall pick number is next.

        Because every pick made is recorded and nothing is ever removed except by
        rewinding, the LENGTH of the log is the draft's progress. That is the
        single most useful consequence of storing only the log.

        Steps:
            1. Count the picks made and add one, since pick numbers start at 1.

        Returns:
            int: The next pick number. Equal to `config.total_picks + 1` once the
                draft is finished, so always check `is_complete` before using it
                as a real pick.
        """
        return len(self.picks) + 1

    @property
    def is_complete(self) -> bool:
        """Report whether every pick in the draft has been made.

        Steps:
            1. Compare the next pick number against the total the league has.

        Returns:
            bool: True when there are no picks left to make.
        """
        return self.current_pick > self.config.total_picks

    @property
    def on_the_clock(self):
        """Work out which team is picking right now.

        Steps:
            1. Return None if the draft is over, since no team is picking.
            2. Ask `snake_order` from draft_model/mechanics.py which team owns
               this pick. It knows the snake rules, including third-round
               reversal, and is the ONLY place those rules live.
            3. Add one to its answer -- see the note below.

        Returns:
            int | None: The drafting team's slot, 1-indexed so 1 is the team
                picking first overall. None when the draft is complete.

        Note:
            `snake_order` returns a 0-INDEXED team id because that is what the
            numpy simulator wants. This class stores 1-indexed slots because that
            is what a person says out loud. The `+ 1` converts between them, and
            forgetting it is the single easiest mistake in this project.
        """
        if self.is_complete:
            return None
        team_id = snake_order(
            self.current_pick, self.config.num_teams, self.config.third_round_reversal
        )
        return team_id + 1

    @property
    def drafted_player_ids(self) -> set:
        """List the model-table ids of everyone picked so far.

        `ffc_player_id` is the PRIMARY key for a pick, not `canonical_id`.
        Canonical ids come from nflreadpy and are nullable by design -- a team
        defense is not a person and never resolves to one -- so keying on them
        means every defense shares the same "missing" id.

        Steps:
            1. Collect each entry's `player_id`, skipping any that is missing.

        Returns:
            set: The model-table ids picked so far, as strings.
        """
        return {p["player_id"] for p in self.picks if p.get("player_id")}

    @property
    def drafted_canonical_ids(self) -> set:
        """List the nflreadpy ids of everyone picked so far.

        The secondary key. Keepers are stored by canonical id in the config, so
        a keeper can be recorded without a table to look his model-table id up
        in. Players with no canonical id are simply absent here and are matched
        by `drafted_player_ids` above instead.

        Steps:
            1. Collect each entry's `canonical_id`, skipping any that is
               missing.

        Returns:
            set: The canonical ids picked so far.
        """
        return {p["canonical_id"] for p in self.picks if p.get("canonical_id")}

    @property
    def unavailable_canonical_ids(self) -> set:
        """List everyone nobody can draft, whether picked yet or not.

        Steps:
            1. Take the canonical ids already picked.
            2. Add every player kept by any team.

        Returns:
            set: The canonical ids that cannot be selected. Keepers always
                resolve to a canonical id, so this needs no player-id half.
        """
        return self.drafted_canonical_ids | set(self.config.kept_player_ids)


    @property
    def state_key(self) -> tuple:
        """A value that changes whenever the draft's contents change.

        Both ids go in, so two picks that differ only in the player-id half --
        two different team defenses, which share a missing canonical id -- are
        still recognised as different drafts.

        Steps:
            1. Pull both ids out of each entry, in order, as pairs.

        Returns:
            tuple: One `(player_id, canonical_id)` pair per pick. Empty before
                the draft starts.

        Note:
            The obvious key would be the NUMBER of picks, which works until the
            first rewind: undo to pick 3, take a different player, and the count
            is identical while the draft is not.
        """
        return tuple((p.get("player_id"), p.get("canonical_id")) for p in self.picks)


    def drafted_mask(self, table) -> np.ndarray:
        """Mark which rows of the player table have already been taken.

        Matches on EITHER id and combines the two with `or`. The model-table id
        is present for every row and catches everyone; the canonical id catches
        keepers, which are recorded before any table lookup happens.

        Steps:
            1. Test each row's `ffc_player_id`, as text, against the drafted
               model-table ids.
            2. Test each row's `canonical_id` against the drafted canonical ids.
            3. Combine with `|`, so a row matching either way counts as taken.

        Args:
            table: The model table, one row per player, in picks-matrix column
                order, with `ffc_player_id` and a nullable `canonical_id`.

        Returns:
            np.ndarray: One True/False per table row, lined up row for row.

        Note:
            `.astype(str)` matters. `ffc_player_id` is numeric in the table and
            stored as text in the log, and `isin` compares by type -- the number
            1 does not match the string "1", so without this nothing matches.
        """
        by_player = table["ffc_player_id"].astype(str).isin(self.drafted_player_ids)
        by_canonical = table["canonical_id"].isin(self.drafted_canonical_ids)
        return (by_player | by_canonical).to_numpy()

    def roster_counts(self, table, pos_index) -> np.ndarray:
        """Count how many players each team already holds at each position.

        Steps:
            1. Build one lookup keyed by BOTH kinds of id, tagging each key so a
               model-table id can never be confused with a canonical one.
            2. Start a grid of zeros shaped (1, teams, positions).
            3. For each pick, find the player's row by model-table id, falling
               back to canonical id.
            4. If a row was found, add one to that team's count at that player's
               position, converting the 1-indexed team slot to a 0-indexed array
               position.
            5. If no row was found, fall back to a position recorded on the pick
               itself. That is how an unlisted pick still counts -- see the note.

        Args:
            table: The model table, in picks-matrix column order.
            pos_index: One position number per player, from
                `draft_model.engine.position_index`, lined up with the rows.

        Returns:
            np.ndarray: An int16 grid shaped (1, num_teams, 6). The leading 1 is
                deliberate -- `monte_carlo_sim` applies a single row to every
                simulation, so this passes straight through.

        Note:
            In a live draft an opponent will take a player FFC has no ADP for, so
            he has no row here. Record that pick with a `position` and it is
            still tallied; record it without one and only the pick number moves.
            Nothing is ever invented -- a position is used only when you supplied
            it.
        """
        rows = {}
        for row, (player_id, canonical_id) in enumerate(
                zip(table["ffc_player_id"].astype(str), table["canonical_id"])):
            rows[("player", player_id)] = row
            cleaned = clean_id(canonical_id)
            if cleaned:
                rows[("canonical", cleaned)] = row

        counts = np.zeros((1, self.config.num_teams, len(POSITIONS)), dtype=np.int16)
        for pick in self.picks:
            row = None
            if pick.get("player_id"):
                row = rows.get(("player", pick["player_id"]))
            if row is None and pick.get("canonical_id"):
                row = rows.get(("canonical", pick["canonical_id"]))

            if row is not None:
                counts[0, pick["team"] - 1, pos_index[row]] += 1
                continue

            # No table row, but the pick may still say WHAT was taken. Recording
            # a position when you cannot name the player is what keeps a team's
            # needs right: without it, an opponent's unlisted kicker leaves their
            # count short and the simulator goes on thinking they need one.
            position = pick.get("position")
            if position in POSITIONS:
                counts[0, pick["team"] - 1, POSITIONS.index(position)] += 1

        return counts


    def make_pick(self, player_id=None, canonical_id=None, source="user",
                  position=None) -> dict:
        """Record one pick and advance the draft.

        Takes both ids because the two callers have different ones to hand. A
        pick from the console has a table row and so both; a keeper is known only
        by canonical id, from the config, with no table in sight.

        Steps:
            1. Refuse if the draft is already finished.
            2. Normalise both ids, turning NaN and blanks into None.
            3. Refuse a pick with neither id -- there would be no way to tell who
               it was.
            4. Refuse a position that is not one this app recognises, so a typo
               fails here rather than silently never matching anything.
            5. Refuse a player already taken under either id.
            6. Refuse a player another team is keeping, unless this IS that
               keeper being recorded.
            7. Append the entry, stamped with the pick number and team.

        Args:
            player_id: The player's `ffc_player_id`, the model table's own key.
                Supply this whenever the pick came from the table.
            canonical_id: The player's nflreadpy id. Nullable -- team defenses
                never have one.
            source: "user", "auto" for a simulated manager, "keeper", or
                "unknown" for a pick whose player you cannot identify.
            position: The player's position, for a pick with no table row to read
                it from. Only useful alongside `source="unknown"`: it lets
                `roster_counts` still tally the pick, so the simulator keeps
                modelling that team's remaining needs correctly.

        Returns:
            dict: The entry just appended, ready to store as-is.

        Raises:
            ValueError: If the draft is complete, if neither id was given, if the
                position is not recognised, if the player is already drafted, or
                if he is kept by another team. Each of the last two would put one
                person on two rosters and corrupt positional need, availability
                and every roster view at once -- with nothing about the result
                looking obviously wrong.
        """
        if self.is_complete:
            raise ValueError(
                f"the draft is complete: all {self.config.total_picks} picks are in"
            )

        player_id = clean_id(player_id)
        canonical_id = clean_id(canonical_id)

        # A pick with no id at all is allowed ONLY when it is explicitly marked
        # unknown. In a live draft an opponent will take somebody you cannot
        # identify -- a kicker outside the pool, a name you did not catch -- and
        # that pick still has to consume a pick number or every later pick is
        # off by one. Requiring the marker keeps a bare make_pick() a loud error.
        if player_id is None and canonical_id is None and source != "unknown":
            raise ValueError(
                'a pick needs a player_id or a canonical_id; use source="unknown" '
                'to record a pick whose player you cannot identify'
            )

        if position is not None and position not in POSITIONS:
            raise ValueError(
                f"{position!r} is not a position; expected one of {POSITIONS}"
            )

        if player_id is not None and player_id in self.drafted_player_ids:
            raise ValueError(f"player {player_id} has already been drafted")
        if canonical_id is not None and canonical_id in self.drafted_canonical_ids:
            raise ValueError(f"{canonical_id} has already been drafted")
        if (canonical_id is not None and source != "keeper"
                and canonical_id in self.config.kept_player_ids):
            raise ValueError(
                f"{canonical_id} is kept by another team and was never available"
            )

        entry = {
            "pick": self.current_pick,
            "team": self.on_the_clock,
            "player_id": player_id,
            "canonical_id": canonical_id,
            "source": source,
            "position": position,
        }
        self.picks.append(entry)
        return entry

    def rewind_to(self, pick_number) -> int:
        """Undo the draft back to a chosen pick, discarding everything after it.

        Undo is `rewind_to(current_pick - 1)`; jumping back several rounds is the
        same call with a smaller number. Because the log is append-only this is
        just a truncation, with no state to unwind.

        Steps:
            1. Refuse a pick number below 1, since pick 1 is the start.
            2. Keep only the entries before that pick. Pick numbers are 1-indexed
               and list slicing is 0-indexed, hence the minus one.
            3. Report how many picks were discarded, so the caller can confirm a
               large rewind with the user.

        Args:
            pick_number: The pick to rewind TO. After this call that pick is the
                next one to be made, so `rewind_to(1)` empties the draft.

        Returns:
            int: How many picks were discarded. Zero when there was nothing after
                that point to remove.

        Raises:
            ValueError: If `pick_number` is less than 1.
        """
        if pick_number < 1:
            raise ValueError(f"cannot rewind to pick {pick_number}; picks start at 1")

        keep = pick_number - 1
        discarded = max(0, len(self.picks) - keep)
        self.picks = self.picks[:keep]
        return discarded

    def apply_keeper_if_due(self) -> bool:
        """Record a keeper automatically if this pick belongs to one.

        A keeper's team spends this pick on him, so nobody chooses anything here
        and the draft should move straight past it. Call this in a loop -- two
        teams can keep on consecutive picks, and one call only handles one.

        Steps:
            1. Do nothing if the draft is finished.
            2. Look the current pick up in `config.keeper_picks`, which maps an
               overall pick number to the player kept there.
            3. If there is one, record him with `make_pick` above, marked
               "keeper" so it is clear nobody chose him.

        Returns:
            bool: True if a keeper was recorded, so the caller knows to look
                again. False when this pick is a normal selection.
        """
        if self.is_complete:
            return False

        canonical_id = self.config.keeper_picks.get(self.current_pick)
        if canonical_id is None:
            return False

        self.make_pick(canonical_id=canonical_id, source="keeper")
        return True




def resimulate(state, board, n_sims=1000) -> np.ndarray:
    """Simulate the rest of the draft from where the board actually stands.

    The pre-draft artifact answers "what happens in a draft where nobody has
    picked yet". One pick in, that assumption is gone. Rather than trying to
    salvage the saved run, this throws a fresh simulation at the real board every
    time -- which costs about a fifth of a second and is exactly right.

    Steps:
        1. Convert the table's position names into the numbers the engine works
           in, using `position_index` from draft_model/engine.py.
        2. Translate the league's keepers into matrix columns with
           `DraftSimService.keeper_columns`.
        3. Run `monte_carlo_sim` from the next pick to the end of the draft,
           handing it the real board: who is already taken, and what every team
           already holds.

    Args:
        state: The draft in progress.
        board: The DraftBoard from `ui_helpers.load_sim_board`, supplying the
            player table and the CALIBRATED sampler settings.
        n_sims: How many drafts to simulate. 1000 is the sweet spot -- roughly
            190ms, and probabilities steady to about a percentage point.

    Returns:
        np.ndarray: An int16 matrix shaped (n_sims, n_players). Entry [s, i] is
            the pick player i went at in simulation s, or UNDRAFTED. Players
            already taken stay UNDRAFTED here, since this only simulates what is
            still to come -- so read availability off it, never draft position.

    Note:
        USE `board.artifact.mu` AND `.sd`, NOT `adp_target`/`stdev_target`. Those
        are the calibrated values the offline run solved for; the raw targets
        produce a draft that looks plausible and is subtly the wrong one.

        No `rng` is passed on purpose. Left out, the run derives from
        `config.random_seed`, so the same board always gives the same numbers.
        Pass one and the percentages would shimmer on every rerun with pure Monte
        Carlo noise, which reads as the market moving when nothing has happened.
    """
    pos_index = position_index(board.table["position"])
    return monte_carlo_sim(
        board.artifact.mu,
        board.artifact.sd,
        pos_index,
        state.config,
        n_sims=n_sims,
        start_pick=state.current_pick,
        end_pick=state.config.total_picks,
        already_drafted=state.drafted_mask(board.table),
        roster_counts=state.roster_counts(board.table, pos_index),
        keeper_picks=DraftSimService.keeper_columns(state.config, board.table),
    )

def remaining_picks(state) -> tuple:
    """List the picks you still have left to use.

    Everything the console shows is measured at these picks, so they get worked
    out once here rather than being recomputed at three call sites.

    Steps:
        1. Start from `config.my_selectable_picks`, which is the picks you own
           MINUS any spent on your own keeper -- a pick already spent is not one
           you can plan a selection at.
        2. Keep only those at or after the current pick.

    Args:
        state: The draft in progress.

    Returns:
        tuple: Your remaining pick numbers, ascending. Empty once your last pick
            has been made, which callers must handle.
    """
    return tuple(p for p in state.config.my_selectable_picks
                 if p >= state.current_pick)


def team_picks_from(state, team_slot, from_pick) -> tuple:
    """List the picks one team still owns, at or after a given pick number.

    The generalisation of `remaining_picks` above, which only ever answers for
    you. The cost-of-waiting panel follows whoever is on the clock, so it needs
    the same question answered about any team.

    Steps:
        1. Ask `picks_for_slot` from draft_model/mechanics.py for every pick that
           team owns. Using it rather than fresh arithmetic keeps snake order --
           third-round reversal included -- in the one place it lives.
        2. Drop any pick spent on that team's own keeper, since a pick already
           spent is not a decision they get to make.
        3. Keep only the picks at or after `from_pick`.

    Args:
        state: The draft in progress.
        team_slot: Which team, 1-indexed. `picks_for_slot` expects the same
            convention, so no conversion is needed here.
        from_pick: The earliest pick to include. Pass `state.current_pick` for
            "everything this team has left".

    Returns:
        tuple: That team's remaining pick numbers, ascending. Empty once they
            have no picks left.
    """
    owned = picks_for_slot(
        team_slot, state.config.num_teams, state.config.num_rounds,
        state.config.third_round_reversal,
    )
    spent = state.config.keeper_picks
    return tuple(p for p in owned if p >= from_pick and p not in spent)


def avail_target_pick(state):
    """Work out which pick the console's availability column should measure.

    Normally the answer is your next pick: "will he still be there when I am
    up?". But when it IS your pick that question is trivial -- he is on the board
    right now, so every available player reads about 100% and the column stops
    saying anything at the exact moment you are choosing.

    On your turn the useful question is the next one along: "if I pass on him,
    does he come back to me?"

    Steps:
        1. Get your remaining picks. If none are left there is nothing to
           measure.
        2. If the team on the clock is yours AND the first remaining pick is this
           very pick, look one further ahead.
        3. If there is nothing further ahead, return None -- this is your last
           pick and there is no later turn to wait for.
        4. Otherwise return your next pick.

    Args:
        state: The draft in progress.

    Returns:
        int | None: The pick number to measure availability at, or None when
            there is nothing useful to show. Callers should drop the column
            entirely on None rather than displaying an empty one.

    Note:
        Step 2 checks `mine[0] == current_pick` rather than just "is it my turn".
        If your own keeper spends the pick you are sitting on, your first
        remaining SELECTABLE pick is already a later one, and skipping ahead
        again would measure the wrong turn.
    """
    mine = remaining_picks(state)
    if not mine:
        return None

    if (state.on_the_clock == state.config.draft_position
            and mine[0] == state.current_pick):
        return mine[1] if len(mine) > 1 else None

    return mine[0]

def live_columns(state, board, picks) -> pd.DataFrame:
    """Build the table of still-available players the console shows.

    Turns the raw simulation matrix into per-player numbers: how likely each man
    is to survive to each of your remaining picks, and what passing on him would
    cost.

    Steps:
        1. Work out which of your picks are still ahead with `remaining_picks`.
        2. Copy the display columns out of the model table, and attach VORP.
        3. If you have picks left, get the whole availability grid in one call to
           `availability_matrix`, then add one `P@<pick>` column per pick.
        4. Work out, per POSITION, the best value expected to survive to your
           pick after next, then subtract it from each player's own value. That
           gap is the cost of waiting on him.
        5. Drop everyone already drafted, and everyone kept by another team.

    Args:
        state: The draft in progress.
        board: The DraftBoard, for the player table, VORP and the kept mask.
        picks: The matrix from `resimulate` above.
    
    Returns:
        pd.DataFrame: One row per AVAILABLE player, carrying `ffc_player_id`,
            `canonical_id`, `name`, `position`, `team`, `adp_target`,
            `projection`, `vorp`, `cost_of_waiting`, and one `P@<pick>` column
            per remaining pick of yours. Row numbers are reset, so they do NOT
            line up with the matrix columns any more -- look players up by
            `ffc_player_id` from here on.

    Note:
        There is no `tier` column: tier comes from UDK's rankings via
        RosterService, and the model table only carries what the simulation
        needs. The page joins it on by `canonical_id`.

        A kept player is dropped rather than shown at 0%. He was never in the
        pool for anybody, so he is not a candidate you passed on.

        `cost_of_waiting` is a SIGNED gap and is not floored at zero. A negative
        value says the position is expected to offer something better than this
        player next round, so the pick is better spent elsewhere -- which is real
        information. An earlier version multiplied the gap by the chance he was
        gone and then clamped it at zero, which drove 89% of the column to
        exactly 0.0 and made it useless.
    """
    mine = remaining_picks(state)
    table = board.table
    gone = state.drafted_mask(table) | board.kept

    # ffc_player_id comes along because it is the PRIMARY key for a pick. The
    # console hands it straight to make_pick, and it is the only id every row is
    # guaranteed to have -- team defenses carry no canonical_id at all.
    frame = table[["ffc_player_id", "canonical_id", "name", "position", "team",
                   "adp_target", "projection"]].copy()
    frame["vorp"] = board.vorp

    if mine:
        # (n_players, len(mine)) -- one probability per player per pick of yours.
        grid = availability_matrix(picks, mine)
        for column, pick in enumerate(mine):
            frame[f"P@{pick}"] = grid[:, column]

        # Your pick after next, or your last one if this is it.
        horizon = mine[1] if len(mine) > 1 else mine[0]
        positions = table["position"].to_numpy()

        # How much better this player is than the best you could expect at his
        # position if you waited a round. Positive means passing costs you that
        # much; negative means the position offers something better later and
        # this pick is better spent elsewhere.
        fallback = best_available_by_position(
            picks, horizon, board.vorp, positions, available_mask=~gone,
        )
        frame["cost_of_waiting"] = (
            frame["vorp"] - frame["position"].map(fallback)
        )
    else:
        # Your last pick is in. Nothing left to wait for.
        frame["cost_of_waiting"] = 0.0

    return frame.loc[~gone].reset_index(drop=True)

def positional_costs_for_team(state, board, picks, team_slot) -> pd.DataFrame:
    """Work out what waiting a round costs ONE team at each position.

    Answers "which position should this pick be spent on?", which is a different
    question from "which player?" and one the per-player numbers cannot answer.
    A deep, interchangeable tier scores near zero however likely each individual
    is to go; one elite player with a cliff behind him scores high.

    Written for any team rather than just yours, so the console can follow
    whoever is on the clock. Watching each manager's urgency in turn is how a run
    becomes visible before it happens.

    Steps:
        1. Get that team's remaining picks with `team_picks_from` above. Two are
           needed -- this compares now against their next turn -- so give up if
           fewer remain.
        2. Build an availability mask of who is genuinely still on the board.
        3. For each position with a usable VORP, record the best still available
           and call `positional_cost_of_waiting` from draft_model/queries.py.
        4. Sort by cost so the most urgent position is first.

    Args:
        state: The draft in progress.
        board: The DraftBoard, for the table, VORP and the kept mask.
        picks: The matrix from `resimulate` above.
        team_slot: Which team's decision to describe, 1-indexed. Pass
            `state.on_the_clock` for whoever is picking now.

    Returns:
        pd.DataFrame: Columns `position`, `best_available_vorp` and `cost`, most
            urgent first. Empty when that team has fewer than two picks left,
            which is a normal end-of-draft state rather than an error.

    Note:
        Passing `available_mask` matters here. Given one, the query treats "best
        available now" as a FACT read off the board instead of a prediction --
        which is right mid-draft, where you can simply see who is left. It also
        means `at_pick` is unused, so the horizon that actually drives the answer
        is the team's NEXT pick.
    """
    mine = team_picks_from(state, team_slot, state.current_pick)
    if len(mine) < 2:
        return pd.DataFrame(columns=["position", "best_available_vorp", "cost"])

    at_pick, next_pick = mine[0], mine[1]
    positions = board.table["position"].to_numpy()
    available = ~(state.drafted_mask(board.table) | board.kept)

    rows = []
    for position in sorted(set(positions)):
        at_position = (positions == position) & np.isfinite(board.vorp) & available
        if not at_position.any():
            continue
        rows.append({
            "position": position,
            "best_available_vorp": float(board.vorp[at_position].max()),
            "cost": positional_cost_of_waiting(
                picks, position, at_pick, next_pick, board.vorp, positions,
                available_mask=available,
            ),
        })

    return pd.DataFrame(rows).sort_values("cost", ascending=False).reset_index(drop=True)


def positional_costs(state, board, picks) -> pd.DataFrame:
    """Work out what waiting a round costs YOU at each position.

    A thin wrapper over `positional_costs_for_team` above, kept because "what
    does this cost me" is the question most callers actually have, and spelling
    out your own draft position at each call site would be noise.

    Steps:
        1. Call `positional_costs_for_team` with your own draft position.

    Args:
        state: The draft in progress.
        board: The DraftBoard, for the table, VORP and the kept mask.
        picks: The matrix from `resimulate` above.

    Returns:
        pd.DataFrame: As `positional_costs_for_team`, for your team.
    """
    return positional_costs_for_team(state, board, picks,
                                     state.config.draft_position)


def state_from_session(session, config) -> DraftState:
    """Rebuild a DraftState from a stored session.

    The join between storage and the draft logic. The repository knows the shape
    of a saved session and nothing about drafts; DraftState knows about drafts
    and nothing about storage. This is the one place the two meet.

    Steps:
        1. Copy the stored pick log into a new list, so appending a pick changes
           the state rather than the dictionary that came out of the database.
        2. Read the seed, defaulting to 0 for a live session where it is unused.

    Args:
        session: A session document from DraftSessionRepo.
        config: The league's DraftConfig, from `DraftConfig.from_draft_doc`. The
            session stores only a `draft_id`, so the caller loads the draft and
            builds this.

    Returns:
        DraftState: The draft exactly where it was left off.

    Note:
        Storing only `draft_id` rather than a copy of the league settings is
        deliberate. Editing a league would leave a stored copy stale, and the
        session would then describe a draft under settings that no longer exist.
    """
    return DraftState(
        config=config,
        picks=list(session.get("picks") or []),
        seed=int(session.get("seed", 0)),
    )

def team_players(state, board, team_slot) -> pd.DataFrame:
    """Collect everything one team has drafted, ready for the roster panel.

    Steps:
        1. Gather the canonical ids that team has taken from the pick log.
        2. Pull those rows out of the model table, keeping the display columns.
        3. Return an empty frame with the right columns if they have nothing yet,
           so the caller can slot it without a special case.

    Args:
        state: The draft in progress.
        board: The DraftBoard, for the player table.
        team_slot: Which team, 1-indexed.

    Returns:
        pd.DataFrame: Columns `canonical_id`, `name`, `position` and
            `projection`, one row per player that team holds.

    Note:
        Players outside the model pool are absent, exactly as in
        `DraftState.roster_counts`. Their pick counted, but there is no row here
        to show, so a live draft's kicker picks simply will not appear.
    """
    player_ids = {p["player_id"] for p in state.picks
                  if p["team"] == team_slot and p.get("player_id")}
    canonical_ids = {p["canonical_id"] for p in state.picks
                     if p["team"] == team_slot and p.get("canonical_id")}
    columns = ["canonical_id", "name", "position", "projection"]
    if not player_ids and not canonical_ids:
        return pd.DataFrame(columns=columns)
    mine = (board.table["ffc_player_id"].astype(str).isin(player_ids)
            | board.table["canonical_id"].isin(canonical_ids))
    return board.table.loc[mine, columns]


def held_mask(state, board, team_slot) -> np.ndarray:
    """Mark which players a team ALREADY holds, row for row against the table.

    The roster-panel question asked as a mask instead of a table, so it can be
    combined with a simulation. `team_players` above answers the same thing for
    display; this answers it for arithmetic.

    Steps:
        1. Gather that team's picks from the log, under both kinds of id.
        2. Test the model table against each set and combine with `or`, the same
           dual-key match `DraftState.drafted_mask` uses and for the same reason:
           a team defense has no canonical id, so a canonical-only test would
           miss it.

    Args:
        state: The draft in progress.
        board: The DraftBoard, for the player table.
        team_slot: Which team, 1-indexed.

    Returns:
        np.ndarray: One True/False per table row. True means that team has him.
    """
    player_ids = {p["player_id"] for p in state.picks
                  if p["team"] == team_slot and p.get("player_id")}
    canonical_ids = {p["canonical_id"] for p in state.picks
                     if p["team"] == team_slot and p.get("canonical_id")}
    by_player = board.table["ffc_player_id"].astype(str).isin(player_ids)
    by_canonical = board.table["canonical_id"].isin(canonical_ids)
    return (by_player | by_canonical).to_numpy()


def projected_roster(state, board, picks, team_slot) -> np.ndarray:
    """Work out a team's FINAL roster -- what they hold plus what they will get.

    This is the join that makes every mid-draft team comparison possible. On its
    own the simulation only describes the picks still to come, and on its own the
    pick log only describes the ones already made; neither is a roster. Together
    they are.

    Steps:
        1. Ask `held_mask` above which players that team has already taken.
        2. Ask `team_picks_from` above which picks they still own, counting from
           the pick the simulation started at.
        3. Ask `roster_from_picks` in draft_model/queries.py which players those
           picks land on in each simulation.
        4. Combine with `|`, so a player counts if they hold him OR are simulated
           to get him.

    Args:
        state: The draft in progress.
        board: The DraftBoard, for the player table.
        picks: The (n_sims, n_players) matrix from `resimulate` above.
        team_slot: Which team, 1-indexed.

    Returns:
        np.ndarray: An (n_sims, n_players) True/False mask of that team's final
            roster. Held players are True in EVERY simulation, since those picks
            have already happened and cannot come out differently.

    Note:
        Step 2 counts from `state.current_pick`, matching the window `resimulate`
        ran over. Passing a matrix simulated from a DIFFERENT starting pick would
        double-count: a player already taken would also be marked as a future
        pick. The two are kept in step by always deriving both from the same
        `state`.
    """
    held = held_mask(state, board, team_slot)
    upcoming = team_picks_from(state, team_slot, state.current_pick)
    return held[None, :] | roster_from_picks(picks, upcoming)


# ---------------------------------------------------------------------------
# Team strengths: how every roster in the league compares
# ---------------------------------------------------------------------------

STRENGTH_POSITIONS = ("QB", "RB", "WR", "TE")
"""Positions the strength panel breaks out. Kickers and defenses are left out
throughout: neither is projected in this app, so every number involving them
would be the same for every team and only make the panel look more precise than
it is."""

LOWER_IS_BETTER = ("Replacement", "Risk")
"""Groups where a SMALL number is the good one. A replacement gap is the drop
from your worst starter to your best bencher, so small means deep; risk is how
shaky a lineup is, so small means dependable."""

BEST_N_UPSIDE = 2
"""How many players an upside number is built from. The question upside answers
is "do I have a ceiling here?", and one genuine lottery ticket is what you want
-- five mediocre players are not equivalent to one explosive one. Averaging the
whole group would cancel a boom against a bust and hide both. Two rather than one
because a single player makes the number jump around."""


def team_strength_table(state, board, picks=None, projected=True,
                        ratings=None) -> pd.DataFrame:
    """Score every team in the league on every category, ready to compare.

    The one calculation behind the whole strengths panel. Both of the panel's
    views -- "where do I rank" and "who is best at this" -- are just two ways of
    reading the table this returns, which is why it is computed once here rather
    than twice in the interface.

    Steps:
        1. Pull the projections and positions out of the player table.
        2. For each team, work out the roster to judge them on, which depends on
           the mode -- see `projected` below.
        3. Line the risk and upside ratings up with the player table using
           `align_ratings` below, if any were supplied.
        4. Hand that roster to `_strength_values` below, which fills the lineup
           and turns it into one number per category.
        5. Collect the teams side by side into a single table.

    Args:
        state: The draft in progress.
        board: The DraftBoard, for the player table and the replacement levels.
        picks: The (n_sims, n_players) matrix from `resimulate` above. Required
            when `projected` is True and ignored otherwise.
        projected: Which question to answer. True gives "projected final" -- what
            each team is HEADING for, with the rest of the draft simulated, via
            `projected_roster` above. False gives "as drafted" -- what each team
            has ACTUALLY got so far, via `held_mask` above.
        ratings: A frame with `canonical_id`, `risk` and `upside`, such as the
            roster service's. Omit it and the risk and upside rows are simply
            left out -- the rest of the panel does not depend on them.

    Returns:
        pd.DataFrame: One row per category and one column per team, the columns
            numbered by draft slot. The row labels are a two-level index of
            (group, category), so the panel can show the groups apart.

    Raises:
        ValueError: If `projected` is True but no picks matrix was supplied.

    Note:
        WHICH MODE YOU ARE IN CHANGES WHAT EVERY NUMBER MEANS, and the difference
        is largest early. In round 2 a projected-final roster is nearly all
        simulation, so the teams cluster and mostly reflect draft slot; by round
        8 it is mostly real and the panel says something about how people have
        actually drafted. Neither is wrong, but a reader who thinks they are
        looking at one while looking at the other will misread it badly.
    """
    if projected and picks is None:
        raise ValueError("projected=True needs the picks matrix from resimulate")

    projections = board.table["projection"].to_numpy()
    positions = board.table["position"].to_numpy()
    adjusted = align_ratings(board, ratings) if ratings is not None else None

    columns = {}
    for team in range(1, state.config.num_teams + 1):
        if projected:
            roster = projected_roster(state, board, picks, team)
        else:
            # One row, because what a team already holds is not uncertain.
            roster = held_mask(state, board, team)[None, :]
        columns[team] = _strength_values(roster, projections, positions,
                                         state.config.starting_slots,
                                         board.replacement, adjusted)

    table = pd.DataFrame(columns)
    table.index = pd.MultiIndex.from_tuples(table.index, names=["Group", "Category"])
    return table


def _strength_values(roster, projections, positions, starting_slots,
                     replacement, adjusted=None):
    """Turn one team's roster into one number per category.

    The scoring rules for all four groups live here, so the two modes above
    cannot drift apart -- both go through this.

    Steps:
        1. Fill the lineup with `lineup_slot_masks` from draft_model/queries.py,
           which says who filled which slot. Merge the slots to get the starters,
           and everyone else on the roster is the bench.
        2. Score each starting slot with `_slot_points` below, which tops an
           unfilled slot up to replacement level.
        3. Add the scorable slots together for the lineup total.
        4. Average each position's bench with `_bench_average` below.
        5. Measure each position's depth with `_replacement_gap` below.
        6. If ratings were supplied, score risk and upside with `_weighted_risk`
           and `_top_upside` above, which aggregate differently on purpose --
           see their docstrings.
        7. Average every category across the simulations.

    Args:
        roster: The (n_sims, n_players) mask of who this team has.
        projections: Projected fantasy points per player.
        positions: The position name for each player.
        starting_slots: How many start at each position.
        replacement: Position -> replacement points, from
            `draft_model.queries.replacement_value`. Positions with no projected
            players are absent, so it is read with `.get`.
        adjusted: The dict from `align_ratings` above, holding adjusted risk and
            upside arrays. None leaves those categories out entirely.

    Returns:
        dict: Maps a (group, category) pair to that team's average value. NaN
            where a category does not apply, such as bench strength at a position
            the team has nobody spare at.
    """
    masks = lineup_slot_masks(roster, projections, positions, starting_slots)

    starters = np.zeros(np.shape(roster), dtype=bool)
    for mask in masks.values():
        starters |= mask
    bench = np.asarray(roster, dtype=bool) & ~starters

    # A flex has no replacement level of its own -- the flex-eligible positions
    # already have it baked in, since replacement_value derives them from a
    # shared pool. Take the best of them: an empty flex is filled by whichever
    # freely-available flex player is best.
    flex_replacement = max(
        (replacement[p] for p in FLEX_POSITIONS if p in replacement),
        default=0.0,
    )

    values = {}
    lineup_total = np.zeros(np.shape(roster)[0], dtype=float)

    for slot in STRENGTH_POSITIONS + ("FLEX",):
        if slot not in masks:
            continue
        level = flex_replacement if slot == "FLEX" else replacement.get(slot, 0.0)
        points = _slot_points(masks[slot], projections, starting_slots[slot], level)
        values[("Starting", slot)] = points.mean()
        lineup_total += points

    values[("Starting", "Lineup total")] = lineup_total.mean()

    for position in STRENGTH_POSITIONS:
        at_position = np.asarray(positions) == position
        values[("Bench", position)] = _bench_average(bench & at_position, projections)

    for slot in STRENGTH_POSITIONS + ("FLEX",):
        if slot not in masks:
            continue
        spare = (np.isin(positions, FLEX_POSITIONS) if slot == "FLEX"
                 else np.asarray(positions) == slot)
        values[("Replacement", slot)] = _replacement_gap(
            masks[slot], bench & spare, projections)

    if adjusted is not None:
        risk, upside = adjusted["risk"], adjusted["upside"]

        # Kickers and defenses are unrated anyway, but excluding them explicitly
        # keeps this the same lineup the points total is built from.
        scorable = starters & ~np.isin(positions, ("K", "DST"))
        values[("Risk", "Lineup")] = _weighted_risk(scorable, risk, projections)
        values[("Upside", "Lineup")] = _top_upside(scorable, upside)

        for slot in STRENGTH_POSITIONS + ("FLEX",):
            if slot not in masks:
                continue
            values[("Risk", slot)] = _weighted_risk(masks[slot], risk, projections)
            values[("Upside", slot)] = _top_upside(masks[slot], upside)

        # Bench RISK is deliberately absent. A bench is where fliers belong, so
        # high risk there is a feature; a metric that penalised it would push you
        # towards a boring bench, which is the opposite of good advice.
        for position in STRENGTH_POSITIONS:
            at_position = np.asarray(positions) == position
            values[("Bench upside", position)] = _top_upside(
                bench & at_position, upside)

    # Ordered so the panel reads top-down: the headline, then the breakdown.
    order = ([("Starting", "Lineup total")]
             + [("Starting", s) for s in STRENGTH_POSITIONS + ("FLEX",)]
             + [("Bench", p) for p in STRENGTH_POSITIONS]
             + [("Replacement", s) for s in STRENGTH_POSITIONS + ("FLEX",)]
             + [("Risk", s) for s in ("Lineup",) + STRENGTH_POSITIONS + ("FLEX",)]
             + [("Upside", s) for s in ("Lineup",) + STRENGTH_POSITIONS + ("FLEX",)]
             + [("Bench upside", p) for p in STRENGTH_POSITIONS])
    return {key: values[key] for key in order if key in values}


def align_ratings(board, ratings) -> dict:
    """Line up the risk and upside ratings with the player table, and adjust them.

    The ratings come from UDK's rankings rather than the model table, so they
    arrive keyed by canonical id and have to be matched onto the table's rows
    before any array maths can use them. The adjustment happens here, once, for
    the reason given in the note.

    Steps:
        1. Match the ratings onto the player table by canonical id, keeping the
           table's row order so the result lines up with every other array.
        2. Hand each rating to `adjust_within_position` in draft_model/queries.py,
           which removes the part that merely restates the projection.

    Args:
        board: The DraftBoard, for the player table.
        ratings: A frame with `canonical_id`, `risk` and `upside`, such as the
            roster service's. Players it does not mention come out as NaN.

    Returns:
        dict: Maps `"risk"` and `"upside"` to an adjusted array, one value per
            table row. NaN for anyone unrated -- every kicker and defense, and
            anyone UDK has not graded.

    Note:
        ADJUSTED AGAINST THE WHOLE POOL, ONCE, not against who is still on the
        board. A player's rating describes him, so it should not drift as other
        people get drafted -- and it would if the comparison group shrank with
        every pick.

        The projection used is the one the panel SHOWS, not UDK's own points
        column. The two agree to within 1% at every position, so the choice
        barely moves the numbers, but making the adjusted rating orthogonal to
        the projection displayed beside it is the point of the exercise.
    """
    keys = board.table[["canonical_id"]]
    matched = keys.merge(ratings[["canonical_id", "risk", "upside"]],
                         on="canonical_id", how="left")

    projections = board.table["projection"].to_numpy()
    positions = board.table["position"].to_numpy()

    return {
        name: adjust_within_position(matched[name].to_numpy(), projections,
                                     positions)
        for name in ("risk", "upside")
    }


def _top_upside(mask, upside, best=BEST_N_UPSIDE) -> float:
    """Score a group of players on their best one or two, not their average.

    "Do I have a ceiling here?" is a question about the best player in a group,
    not the typical one. Averaging would let a bust cancel a boom and report a
    team with both as unremarkable, and summing would just reward owning more
    players.

    Steps:
        1. Score the rated players in the group, pushing everyone else to
           negative infinity so they sort to the back.
        2. Sort each simulation's row best first and keep the top few.
        3. Drop the placeholders, so a group of one is scored on that one rather
           than being dragged down by an imaginary second player.
        4. Average across the simulations.

    Args:
        mask: The (n_sims, n_players) mask of the group being scored.
        upside: Adjusted upside per player. NaN where unrated.
        best: How many of the best to average.

    Returns:
        float: The average of the group's best few. NaN if the group never
            contains a rated player.
    """
    values = np.asarray(upside, dtype=float)
    scores = np.where(mask & np.isfinite(values), values, -np.inf)

    # Negating turns numpy's ascending sort into a descending one.
    top = -np.sort(-scores, axis=1)[:, :best]

    real = np.isfinite(top)
    counts = real.sum(axis=1)
    totals = np.where(real, top, 0.0).sum(axis=1)

    per_sim = np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)
    return float(np.nanmean(per_sim)) if np.isfinite(per_sim).any() else float("nan")


def _weighted_risk(mask, risk, projections) -> float:
    """Measure how much of a group's SCORING sits behind shaky players.

    The question risk answers is "how exposed am I?", and exposure is about
    points rather than headcount. A shaky third receiver barely matters; a shaky
    first-round back matters enormously. Weighting each player's risk by his
    projection is what tells those two apart -- a plain average treats them the
    same.

    Steps:
        1. Use each rated player's projection as his weight, and zero for anyone
           outside the group or without a rating.
        2. Add up risk times weight, and divide by the total weight.
        3. Leave NaN for a simulation where the group has nobody rated.
        4. Average across the simulations.

    Args:
        mask: The (n_sims, n_players) mask of the group being scored.
        risk: Adjusted risk per player. NaN where unrated.
        projections: Projected points per player, used as the weights.

    Returns:
        float: The projection-weighted average risk. NaN if the group never
            contains a rated player.
    """
    values = np.asarray(risk, dtype=float)
    points = np.asarray(projections, dtype=float)

    rated = mask & np.isfinite(values) & np.isfinite(points)
    weights = np.where(rated, points, 0.0)

    totals = (weights * np.nan_to_num(values, nan=0.0)).sum(axis=1)
    divisor = weights.sum(axis=1)

    per_sim = np.where(divisor > 0, totals / np.maximum(divisor, 1e-9), np.nan)
    return float(np.nanmean(per_sim)) if np.isfinite(per_sim).any() else float("nan")


def _slot_points(mask, projections, count, replacement) -> np.ndarray:
    """Score one starting slot, topping up anything the team has not filled.

    Steps:
        1. Add up the projections of whoever is filling the slot.
        2. Count how many of the slot's places are empty.
        3. Add replacement-level points for each empty place.

    Args:
        mask: The (n_sims, n_players) mask for this slot alone.
        projections: Projected points per player. NaN counts as zero.
        count: How many players this slot starts.
        replacement: Points for a freely-available player at this position.

    Returns:
        np.ndarray: One score per simulation, shape (n_sims,).

    Note:
        THE TOP-UP IS WHAT MAKES THE TWO MODES COMPARABLE. A team with no tight
        end has not scored zero at tight end -- they would stream one. Leaving
        the slot at zero would make an ordinary hole look catastrophic, and it
        would make "as drafted" unreadable in round 2, when almost every slot is
        empty. Replacement level still shows the hole as weakness, because it is
        far below what a real starter is worth; it just does not exaggerate it.
    """
    values = np.nan_to_num(np.asarray(projections, dtype=float), nan=0.0)
    filled = mask.sum(axis=1)
    return (mask * values).sum(axis=1) + (count - filled) * replacement


def _bench_average(mask, projections) -> float:
    """Work out what a team's typical fallback at one position is worth.

    An average is the right summary here, unlike for upside: the question is
    "if I need to plug someone in, what am I plugging in?", and that genuinely
    is a typical value rather than a best case.

    Steps:
        1. Count the bench players at this position in each simulation.
        2. Add up their projections and divide, leaving NaN where there are none.
        3. Average across the simulations, ignoring the NaNs.

    Args:
        mask: The (n_sims, n_players) mask of bench players at one position.
        projections: Projected points per player.

    Returns:
        float: The average bench projection, or NaN if the team never has a
            spare player at that position -- which is itself worth seeing, and is
            not the same as "their bench is worth zero".
    """
    values = np.nan_to_num(np.asarray(projections, dtype=float), nan=0.0)
    counts = mask.sum(axis=1)
    totals = (mask * values).sum(axis=1)

    with np.errstate(invalid="ignore", divide="ignore"):
        averages = np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)
    return float(np.nanmean(averages)) if np.isfinite(averages).any() else float("nan")


def _replacement_gap(starters, bench, projections) -> float:
    """Measure how far a team falls off if a starter goes down.

    Worst starter minus best bencher, at one position. This measures something
    VORP structurally cannot: VORP compares a player to what is freely available
    in the LEAGUE, while this compares him to what you ALREADY OWN. Late in a
    draft those are very different questions, and this is the one that says
    whether your next pick should be a starter upgrade or insurance.

    Steps:
        1. Find the lowest projection among the slot's starters, per simulation.
        2. Find the highest projection among the spare players at that position.
        3. Subtract, leaving NaN where either side is missing.
        4. Average across the simulations.

    Args:
        starters: The (n_sims, n_players) mask for one starting slot.
        bench: The (n_sims, n_players) mask of spare players eligible for it.
        projections: Projected points per player.

    Returns:
        float: The average gap. Lower means deeper. NaN if the team has no
            starter or no backup at that position, since there is no gap to
            measure rather than a gap of zero.

    Note:
        This is always zero or positive, because the lineup is filled greedily by
        projection -- the best bencher can never out-project the worst starter.
        It is a depth measure, never a lineup mistake.
    """
    values = np.nan_to_num(np.asarray(projections, dtype=float), nan=0.0)

    # Infinity is the identity for min and max, so a simulation with nobody on
    # one side comes out infinite and is dropped rather than distorting the mean.
    worst = np.where(starters, values, np.inf).min(axis=1)
    best = np.where(bench, values, -np.inf).max(axis=1)

    gaps = worst - best
    gaps = gaps[np.isfinite(gaps)]
    return float(gaps.mean()) if gaps.size else float("nan")


# ---------------------------------------------------------------------------
# Draft Sim: the eleven other teams pick themselves
# ---------------------------------------------------------------------------


def session_board(state, board) -> np.ndarray:
    """Draw the one board this simulated draft's AI managers pick from.

    Each simulated manager forms an opinion of every player ONCE, at the start of
    the draft, and keeps it. Re-drawing every pick would make them forget: a
    player could be nearly taken at pick 10 and inexplicably survive to pick 60,
    because nobody would remember having wanted him.

    Recomputed on demand rather than stored. It is a pure function of the
    session's seed, so recomputing is cheap, always consistent, and cannot go
    stale the way a cached copy could.

    Steps:
        1. Call `draw_boards_for_sims` from draft_model/engine.py for a single
           simulation, seeded from the session.

    Args:
        state: The draft in progress, supplying the seed.
        board: The DraftBoard, for the calibrated sampler settings.

    Returns:
        np.ndarray: A float32 array shaped (1, n_players, num_teams). Entry
            [0, i, t] is what manager t thinks of player i. LOWER IS BETTER --
            these behave like pick numbers.

    Note:
        Because this depends only on the seed, and `auto_pick` below depends only
        on this and the pick history, the whole simulated draft is reproducible.
        Rewind ten picks and play forward and the AI makes exactly the same
        choices -- which is what lets you replay one of YOUR decisions
        differently against an unchanged field.
    """
    return draw_boards_for_sims(
        board.artifact.mu, board.artifact.sd, state.config.num_teams,
        state.seed, [0],
    )


def auto_pick(state, board, boards) -> dict:
    """Work out who the team on the clock takes, and record it.

    Uses the real simulator for exactly one pick, so the AI managers behave
    identically to the ones behind your availability percentages. A separate
    "good enough" heuristic here would be a second source of truth, and the two
    would drift until the probabilities described a different draft from the one
    on screen.

    Steps:
        1. Convert positions to the numbers the engine works in.
        2. Run `sim_batch` for a single pick, with `start_pick` and `end_pick`
           both set to the current pick, handing it the real board state.
        3. Find the column the simulator marked with that pick number.
        4. Look that row up and record the pick, marked "auto".

    Args:
        state: The draft in progress.
        board: The DraftBoard, for the player table.
        boards: The session's board, from `session_board` above. Passed in rather
            than redrawn so every pick in one draft uses the same opinions.

    Returns:
        dict: The pick entry just recorded.

    Raises:
        ValueError: If the simulator selected nobody, which means the pool ran
            dry. Loud on purpose: silently skipping a pick would leave the draft
            one selection short with no sign of why.

    Note:
        `keeper_picks` is passed even though keepers are recorded by
        `apply_keeper_if_due` rather than here. It is what marks kept players as
        off the board, so the AI cannot draft somebody another team is keeping.
    """
    pos_index = position_index(board.table["position"])
    pick_number = state.current_pick

    result = sim_batch(
        boards, pos_index, state.config,
        start_pick=pick_number,
        end_pick=pick_number,
        already_drafted=state.drafted_mask(board.table),
        roster_counts=state.roster_counts(board.table, pos_index),
        keeper_picks=DraftSimService.keeper_columns(state.config, board.table),
    )

    chosen = np.flatnonzero(result[0] == pick_number)
    if not len(chosen):
        raise ValueError(
            f"the simulator selected nobody at pick {pick_number}; the player "
            f"pool is exhausted"
        )

    row = board.table.iloc[int(chosen[0])]
    return state.make_pick(player_id=row["ffc_player_id"],
                           canonical_id=row["canonical_id"], source="auto")


def advance_until_your_turn(state, board, boards, limit=None) -> int:
    """Run AI picks until it is your turn, or the draft ends.

    Backs the "to my pick" button, and doing it in one rerun rather than one
    every three seconds is the difference between a usable fast-forward and
    waiting a minute.

    Steps:
        1. Loop while the draft is unfinished.
        2. Record any keeper due first, since that pick is already spent.
        3. Stop as soon as the team on the clock is yours.
        4. Otherwise take one AI pick and count it.
        5. Stop early if a limit was given and reached -- a guard against a bug
           here spinning forever.

    Args:
        state: The draft in progress.
        board: The DraftBoard.
        boards: The session's board from `session_board` above.
        limit: The most picks to make in one call. None means no limit.

    Returns:
        int: How many picks were made, keepers included.
    """
    made = 0
    while not state.is_complete:
        if state.apply_keeper_if_due():
            made += 1
            continue
        if state.on_the_clock == state.config.draft_position:
            break
        auto_pick(state, board, boards)
        made += 1
        if limit is not None and made >= limit:
            break
    return made


# ---------------------------------------------------------------------------
# Your saved draft plan, matched against the console
# ---------------------------------------------------------------------------


def round_of_pick(pick_number, num_teams) -> int:
    """Work out which round an overall pick number falls in.

    Steps:
        1. Subtract 1 so the arithmetic counts from zero, divide by the league
           size, then add 1 to get back to a 1-indexed round.

    Args:
        pick_number: An overall pick number, 1-indexed.
        num_teams: League size.

    Returns:
        int: The round, 1-indexed.
    """
    return (pick_number - 1) // num_teams + 1


def planned_names_for_round(plan, round_number) -> set:
    """Collect every player you planned for one round, across all positions.

    A saved plan is keyed by `(round label, position)`, so one round's targets
    are spread over four entries -- one per position tab you filled in.

    Steps:
        1. Walk the plan's keys.
        2. Keep the entries whose label belongs to this round, comparing only the
           part before the dot -- see the note.
        3. Pool their players into one set.

    Args:
        plan: The mapping from `DraftPlanService.get_plan`, keyed by a
            `(round_label, position)` tuple such as `("3.04", "RB")`.
        round_number: Which round, 1-indexed.

    Returns:
        set: The player display names planned for that round. Empty when nothing
            was planned, which is normal.

    Note:
        Matching on the ROUND rather than the whole label is deliberate.
        `DraftPlanService.pick_labels` builds those labels with its own snake
        arithmetic that does not know about third-round reversal, so in a league
        with reversal the pick-in-round half of the label disagrees with the
        model's own pick numbers. The round half is right either way, and you
        only own one pick per round, so nothing is lost by ignoring the rest.
    """
    wanted = str(round_number)
    names = set()
    for (round_label, _position), players in (plan or {}).items():
        if str(round_label).split(".")[0] == wanted:
            names.update(players or [])
    return names


def planned_canonical_ids(plan, round_number, roster) -> tuple:
    """Turn the names in your plan into ids the console can actually match on.

    The plan stores DISPLAY NAMES, taken from the roster service. The console
    works from the model table, whose names come from FFC. Those two sources
    spell people differently -- "Kenneth Walker III" against "Kenneth Walker" --
    so matching the two by name would quietly miss players, and a highlight that
    is sometimes missing is worse than no highlight at all.

    Both sides do agree on canonical id, so the names are resolved to ids here
    and the matching happens on those.

    Steps:
        1. Collect the names planned for this round.
        2. Build a lookup from display name to canonical id out of the roster,
           which carries both columns.
        3. Map each planned name through it.
        4. Keep the ones that resolved, and report the ones that did not rather
           than dropping them silently.

    Args:
        plan: The mapping from `DraftPlanService.get_plan`.
        round_number: Which round's targets to look up, 1-indexed.
        roster: The frame from `RosterService.roster()`, with `display_name` and
            `canonical_id` columns.

    Returns:
        tuple: `(ids, unresolved)`. `ids` is a set of canonical ids to highlight;
            `unresolved` is a sorted list of planned names that matched nobody,
            so the page can say so instead of quietly showing fewer stars than
            you planned.
    """
    names = planned_names_for_round(plan, round_number)
    if not names:
        return set(), []

    id_by_name = dict(zip(roster["display_name"], roster["canonical_id"]))

    ids, unresolved = set(), []
    for name in names:
        canonical_id = clean_id(id_by_name.get(name))
        if canonical_id:
            ids.add(canonical_id)
        else:
            unresolved.append(name)

    return ids, sorted(unresolved)

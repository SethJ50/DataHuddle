"""League configuration and the model's tunable constants.

Every number in the CONSTANTS block is a judgment call, not something fitted from
data. They are gathered here rather than scattered through the code so that a
future reader can see the whole set of assumptions at once -- and so nobody
mistakes them for values the data chose.

See draft_model/DESIGN.md 12 for the reasoning behind each.
"""

from dataclasses import dataclass, field
from functools import cached_property

from draft_model.mechanics import picks_for_slot
from scoring import ScoringFormat

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNDRAFTED = 999
"""Sentinel for 'never selected'. Must exceed any real pick number, because
availability queries rely on `pick >= target` being true for undrafted players.
Every statistic over pick numbers must mask this out or it will drag averages
into nonsense."""

RHO = 0.35
"""How much the simulated managers agree with each other WITHIN one draft.

0.0 = twelve fully independent opinions; 1.0 = twelve identical boards.

This cannot be fitted from ADP and stdev -- raising it just makes calibration
refit `sd` around it and hit the same targets either way. It is a documented
judgment value, and pretending otherwise would produce a confident-looking
number that means nothing. Revisit only once real draft logs exist."""

ALPHA = 0.7
"""Damping on the calibration update. A gain-of-1 fixed point on a noisy
objective oscillates instead of converging; this costs a couple of extra
iterations and buys monotone convergence."""

NEED_BONUS = 15.0
"""Board-value units a manager will reach past to fill an empty starter slot.
Along with STARTER_DEADLINE, this is what makes positional runs EMERGE -- runs
are not programmed anywhere. Once two managers take tight ends, the remaining
TE-less managers start applying this and reaching, and the clustering appears
on its own."""

BLOCK = 10_000.0
"""Added to the value of a player at a position the manager has already filled.
Large enough to push him below every real candidate, so he is effectively
unpickable without needing a separate 'is this legal' branch."""

HARD_LIMIT = {"QB": 2, "RB": 6, "WR": 6, "TE": 2, "K": 1, "DST": 1}
"""Most players a simulated manager will roster at each position. Too tight and
the board can lock up (validate_sim's pick-count check catches that); too loose
and simulated rosters stop resembling real ones."""

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
"""Canonical position order. The INDEX of a position in this tuple is how it is
represented inside the simulator -- numpy has no notion of a "QB", so positions
become small integers and every per-position constant becomes an array indexed
the same way. Changing this order changes nothing as long as it's changed here
only; hardcoding an index anywhere else would break that."""

STARTER_DEADLINE = {"QB": 100, "RB": 60, "WR": 60, "TE": 100, "K": 170, "DST": 170}
"""Pick number past which a manager holding ZERO players at a position starts
reaching for one. The K/DST values are load-bearing now that those positions are
in the pool -- they are what stops the simulator drafting kickers in round 8."""

PLATFORM_WEIGHT = 0.5
"""How far to shift FFC's ADP toward your platform's. 0.0 = pure FFC.
Worth a sensitivity sweep: if moving this 0 -> 1 barely changes availability,
the whole shift mechanism isn't earning its complexity."""

POOL_MULTIPLIER = 1.5
"""Drop players with adp beyond total_picks * this. A player with ADP 400 in a
180-pick draft contributes nothing and costs as much to simulate as anyone.
Measured note: FFC returns ~246 players for a 180-pick draft, so this currently
never binds -- keep it as a guard, but expect it to be a no-op."""

MIN_STDEV = 0.5
"""Hard floor on any width, applied last. A zero or near-zero stdev makes a
player perfectly deterministic in the sampler, which is both wrong and a
division hazard. The fallback chain should never produce one -- this exists so
that if it ever does, the failure is bounded instead of silent."""


DEFAULT_STARTING_SLOTS = {
    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1,
}
"""Fallback lineup for drafts saved before starting_slots existed."""


# ---------------------------------------------------------------------------
# Keepers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Keeper:
    """One player a team keeps, and the round they spend to keep him.

    A keeper is a player a team holds onto from last season instead of drafting
    someone new. The team pays for him with a specific pick: name the round, and
    the pick that team owns in that round is consumed by the keeper rather than
    used to select anybody.

    Stored as team-and-round rather than as an overall pick number on purpose.
    Overall pick numbers move when the league size changes, so "team 5 keeps him
    in round 3" survives an edit that "pick 29" would not. `DraftConfig.
    keeper_picks` derives the overall number from snake order when it is needed.

    Ordered, so a tuple of these sorts predictably -- which is what lets
    DraftConfig.fingerprint hash them without the entry order mattering.

    Attributes:
        team: The keeping team's draft slot, 1-indexed, so 1 is the team picking
            first overall. NOT the same as a team id inside the simulator, which
            counts from 0.
        round: Which round the keeper is spent in, 1-indexed.
        canonical_id: The kept player's stable nflreadpy id.
    """

    team: int
    round: int
    canonical_id: str


def normalize_keepers(raw) -> tuple:
    """Read stored keeper data in any of its shapes, and sort it into two piles.

    Keepers used to be saved as a bare list of player ids with no team or round.
    Those records still exist, and they cannot be simulated: without a round
    there is no pick to consume. Rather than guessing a round or throwing the
    players away, this separates the ones that are ready to use from the ones
    still needing a team and a round, so the UI can ask you to finish them.

    Steps:
        1. Return two empty tuples if there is nothing stored.
        2. Walk each entry. A plain string is the legacy shape, so record the id
           as unassigned.
        3. An entry that is already a Keeper is passed straight through, which
           makes calling this twice harmless.
        4. Otherwise treat it as a dictionary. Pull out the team, the round, and
           the player id.
        5. If either the team or the round is missing, record the player as
           unassigned; skip the entry entirely if it has no player id at all.
        6. Otherwise build a Keeper, converting the team and round to whole
           numbers in case they came back from storage as text.
        7. Return both piles, sorted so the order does not depend on how the
           entries happened to be stored.

    Args:
        raw: Whatever was stored under the draft's `keepers` field. May be None,
            a list of player id strings (the legacy shape), a list of
            dictionaries with `team`, `round`, and `canonical_id`, or a list of
            Keeper objects.

    Returns:
        tuple: `(assigned, unassigned)`. `assigned` is a tuple of Keeper records
            ready to simulate, sorted by team then round. `unassigned` is a tuple
            of canonical ids that still need a team and a round before the draft
            can be simulated.

    Raises:
        ValueError: If a team or round is present but is not a whole number.
    """
    if not raw:
        return (), ()

    assigned, unassigned = [], []
    for entry in raw:
        if isinstance(entry, str):
            unassigned.append(entry)
            continue
        if isinstance(entry, Keeper):
            assigned.append(entry)
            continue

        canonical_id = entry.get("canonical_id")
        if not canonical_id:
            continue
        team, round_number = entry.get("team"), entry.get("round")
        if team is None or round_number is None:
            unassigned.append(canonical_id)
            continue
        assigned.append(Keeper(int(team), int(round_number), str(canonical_id)))

    return tuple(sorted(assigned)), tuple(sorted(unassigned))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DraftConfig:
    """Everything about one league that the model needs to know.

    Frozen, and passed as a single object to every function that needs league
    context rather than threading loose parameters around. That prevents the
    classic bug where the simulation runs 12-team but a later query assumes
    10-team -- the two can no longer disagree, because there is only one object.

    Attributes:
        year: Season. Required by the FFC pull and by any historical lookup.
        num_teams: League size.
        num_rounds: Rounds drafted.
        draft_position: YOUR slot, 1-indexed (1 = first overall pick).
        scoring_format: Drives which FFC pool is used.
        platform: Where the league actually drafts ("espn"/"yahoo"/"sleeper").
            Weighted up in the ADP blend, because the default player list a
            platform shows in-app anchors your real leaguemates far more than
            any consensus ranking does.
        starting_slots: Position -> starters. Sets the VORP replacement level,
            which is why hardcoding replacement ranks breaks at any other size.
        keepers: A tuple of Keeper records, one per keeping team. Each names a
            team, the round it spends, and the player held. Empty for redraft
            leagues. The kept player is off the board from pick 1, and the pick
            his team owns in that round is consumed by him.
        roster_size: Total roster slots, distinct from num_rounds (IR/taxi).
        third_round_reversal: True if round 3 repeats round 2's order.
        random_seed: Stored so a surprising result is reproducible.
    """

    year: int
    num_teams: int
    num_rounds: int
    draft_position: int
    scoring_format: ScoringFormat
    platform: str = "espn"
    starting_slots: dict = field(default_factory=lambda: dict(DEFAULT_STARTING_SLOTS))
    keepers: tuple = ()
    roster_size: int | None = None
    third_round_reversal: bool = False
    random_seed: int = 20260730

    def __post_init__(self):
        """Reject impossible leagues at construction time.

        Python calls this automatically right after a DraftConfig is built.
        Checking here is cheap, and it turns a confusing downstream failure (an
        empty simulation, an index error a hundred lines away) into an obvious
        message at the point the bad value was actually supplied.

        Steps:
            1. Reject a league with fewer than two teams, since one team cannot
               draft against anyone.
            2. Reject a draft with no rounds.
            3. Reject a draft position outside the range of real slots, which is
               1 up to the number of teams.
            4. Reject any keeper whose team or round falls outside the league,
               since a keeper on a pick that does not exist could not be spent.
            5. Reject two keepers on the same team-and-round, which would want
               one pick to be consumed twice.
            6. Reject the same player being kept by two different teams.

        Returns:
            None: Returning normally means the configuration is usable.

        Raises:
            ValueError: If any check fails. The message names the offending
                value so the caller can see what it passed.
        """
        if self.num_teams < 2:
            raise ValueError(f"num_teams must be at least 2, got {self.num_teams}")
        if self.num_rounds < 1:
            raise ValueError(f"num_rounds must be at least 1, got {self.num_rounds}")
        if not 1 <= self.draft_position <= self.num_teams:
            raise ValueError(
                f"draft_position {self.draft_position} outside 1..{self.num_teams}"
            )

        # Accept a list of plain dictionaries as well as Keeper records, since
        # that is what comes back out of storage and what a test is likely to
        # write by hand. Anything else -- notably the bare id strings keepers
        # used to be saved as -- is refused with a message that says what to do,
        # rather than failing later with an unhelpful attribute error.
        coerced = []
        for keeper in self.keepers:
            if isinstance(keeper, Keeper):
                coerced.append(keeper)
            elif isinstance(keeper, dict):
                coerced.append(Keeper(
                    int(keeper["team"]), int(keeper["round"]),
                    str(keeper["canonical_id"]),
                ))
            else:
                raise TypeError(
                    f"keeper {keeper!r} is not a Keeper. Keepers now carry a team "
                    f"and a round as well as a player; build them with "
                    f"Keeper(team, round, canonical_id) or normalize_keepers()."
                )
        object.__setattr__(self, "keepers", tuple(coerced))

        seen_slots, seen_players = set(), set()
        for keeper in self.keepers:
            if not 1 <= keeper.team <= self.num_teams:
                raise ValueError(
                    f"keeper team {keeper.team} outside 1..{self.num_teams}"
                )
            if not 1 <= keeper.round <= self.num_rounds:
                raise ValueError(
                    f"keeper round {keeper.round} outside 1..{self.num_rounds}"
                )
            slot = (keeper.team, keeper.round)
            if slot in seen_slots:
                raise ValueError(
                    f"two keepers on team {keeper.team} round {keeper.round}; "
                    f"one pick cannot be spent twice"
                )
            seen_slots.add(slot)
            if keeper.canonical_id in seen_players:
                raise ValueError(
                    f"player {keeper.canonical_id} is kept by more than one team"
                )
            seen_players.add(keeper.canonical_id)

    @property
    def total_picks(self) -> int:
        """Work out how many selections happen in the whole draft.

        Used all over the model as the upper bound on a pick number: the size of
        the simulated draft, and the cutoff past which a player is out of reach.

        Steps:
            1. Multiply the number of teams by the number of rounds, since every
               team picks exactly once per round.

        Returns:
            int: Total picks in the draft, for example 180 for a 12-team,
                15-round league.
        """
        return self.num_teams * self.num_rounds

    @cached_property
    def keeper_picks(self) -> dict:
        """Work out which overall pick number each keeper consumes.

        Keepers are stored as a team and a round, because that survives a change
        to the league that a bare pick number would not. This is where that pair
        becomes the concrete pick the simulator has to reserve.

        Steps:
            1. Start an empty mapping.
            2. For each keeper, call `picks_for_slot` from
               draft_model/mechanics.py to list every pick that team owns. Using
               that function rather than fresh arithmetic is deliberate: it is
               the one place snake order lives, so third-round reversal is
               handled here for free and cannot drift.
            3. Take the entry for the keeper's round. Rounds are 1-indexed and
               the list is 0-indexed, hence the minus one.
            4. Record that pick number against the kept player.

        Returns:
            dict: Maps an overall pick number to the canonical id of the player
                kept there. Empty for a redraft league. Every key is a real pick
                in this draft, because `__post_init__` already rejected any
                keeper outside the league's shape.
        """
        picks = {}
        for keeper in self.keepers:
            owned = picks_for_slot(
                keeper.team, self.num_teams, self.num_rounds,
                self.third_round_reversal,
            )
            picks[owned[keeper.round - 1]] = keeper.canonical_id
        return picks

    @cached_property
    def my_selectable_picks(self) -> tuple:
        """Work out which of your picks you actually get to choose a player with.

        The same as `my_picks` below, minus any pick your own keeper consumes.
        If you keep a player in round 3, your round-3 pick is already spent, and
        showing availability at a pick you cannot use would invite planning
        around a turn that never comes.

        Steps:
            1. Read `keeper_picks` above for the pick numbers keepers consume.
            2. Keep only the picks you own that are not among them.

        Returns:
            tuple: Your pick numbers, ascending, excluding any spent on your own
                keeper. Identical to `my_picks` in a redraft league.
        """
        spent = self.keeper_picks
        return tuple(pick for pick in self.my_picks if pick not in spent)

    @cached_property
    def kept_player_ids(self) -> frozenset:
        """List every player kept by anyone in this league.

        A convenience for the common question "is this player gettable at all?".
        A frozenset because the only operation needed is a membership test, and
        sets answer that instantly however many keepers there are.

        Steps:
            1. Collect the canonical id off each keeper into a frozenset.

        Returns:
            frozenset: The canonical ids of every kept player. Empty for a
                redraft league.
        """
        return frozenset(keeper.canonical_id for keeper in self.keepers)

    @cached_property
    def my_picks(self) -> tuple:
        """Work out which pick numbers in the draft belong to you.

        Marked `cached_property`, meaning it is computed the first time it is
        read and the answer reused afterwards. That is safe here because the
        config is frozen and the inputs can never change.

        Steps:
            1. Call `picks_for_slot` from draft_model/mechanics.py, handing it
               your draft slot, the league size, the round count, and whether
               round 3 reverses. That function knows the snake-order rules.

        Returns:
            tuple: The absolute pick numbers you own, ascending — one per round,
                for example (5, 20, 29, ...) for slot 5 in a 12-team draft.
                "Absolute" means counted from the start of the draft, so pick 20
                is the 20th overall selection rather than the 20th of a round.

        Note:
            DERIVED, never stored. A stored copy could fall out of sync with
            num_teams or num_rounds, and silently disagreeing league parameters
            is exactly what this class exists to prevent.

            These are the only picks any Draft Plan output is evaluated at -- in a
            snake draft that's ~15 numbers, not 200, which is what keeps the
            in-draft queries cheap.
        """
        return picks_for_slot(
            self.draft_position, self.num_teams, self.num_rounds,
            self.third_round_reversal,
        )

    def fingerprint(self) -> str:
        """Build a short ID summarizing every input the SIMULATION depends on.

        A fingerprint is a short string computed from data such that the same
        inputs always produce the same string. This one goes in the saved
        simulation's filename, so a config change that would alter the results
        automatically points at a different file.

        Steps:
            1. Import hashlib here rather than at the top of the module, since
               this is the only place that needs it.
            2. Gather the fields that genuinely change the simulation into a
               tuple, sorting the keepers so that listing the same players in a
               different order still fingerprints the same.
            3. Render that tuple to text, hash it with SHA-256, and keep the
               first 12 characters.

        Returns:
            str: 12 hexadecimal characters, for example "a3f19c02b8de".

        Note:
            Used in the artifact filename, so changing anything that would alter
            the picks matrix mints a NEW artifact rather than silently serving a
            stale one computed under different assumptions.

            WHAT IS DELIBERATELY EXCLUDED, and why it matters:

              draft_position  -- decides which picks you LOOK at, not how the
                                 draft unfolds. my_picks is derived at query time.
              starting_slots  -- sets the VORP replacement level, which is
                                 computed fresh from the table whenever a board
                                 is loaded. It never enters the simulation.
              roster_size     -- bookkeeping only; the draft is num_rounds long.

            Including them would be the safe-looking choice, but it means editing
            your lineup or moving your draft slot forces a full re-simulation
            that produces a byte-identical matrix. Everything excluded here is
            recomputed on load, so a stale value cannot survive.

            random_seed IS included: a different seed is genuinely a different run.
            So is `platform` -- it re-weights the ADP blend, which moves
            adp_target (measured: mean 2.3 picks, up to 11.8), which changes the
            simulation. Before it was a field here, switching platforms silently
            served the previous platform's matrix.
        """
        import hashlib

        parts = (
            self.year, self.num_teams, self.num_rounds, self.scoring_format.value,
            self.platform, tuple(sorted(self.keepers)),
            self.third_round_reversal, self.random_seed,
        )
        return hashlib.sha256(repr(parts).encode()).hexdigest()[:12]

    @classmethod
    def from_draft_doc(cls, doc: dict, year: int, **overrides):
        """Build a config from a draft the user previously saved.

        The bridge between what the app stores in MongoDB and what the model
        needs. Being a `classmethod` means it is called on the class itself, as
        `DraftConfig.from_draft_doc(...)`, and builds a new instance rather than
        working on an existing one.

        Steps:
            1. Copy each stored field into a dictionary of constructor
               arguments. Required fields are read with square brackets so a
               genuinely malformed draft fails loudly.
            2. Read the newer fields with `.get`, supplying a default, so drafts
               saved before those fields existed still load.
            3. Turn the stored scoring format string back into a ScoringFormat,
               so the rest of the model gets the enum rather than a bare string.
            4. Sort the stored keepers with `normalize_keepers` above. If the
               draft says it has keepers but some still lack a team or a round,
               refuse to build a config at all — see Raises for why.
            5. Drop every keeper when the draft's `has_keepers` flag is off, so
               turning keepers off cannot leave one quietly in effect.
            6. Apply any overrides on top, letting a caller change one field
               without rebuilding the whole dictionary.
            7. Build and return the DraftConfig, which runs `__post_init__`
               above and so validates the values.

        Args:
            doc: A draft document from DraftService, holding `num_teams`,
                `draft_position`, `num_rounds`, `platform`, `scoring_format`
                (stored as its text value), and optionally the newer
                `starting_slots`, `has_keepers`, `keepers`, and `roster_size`
                fields.
            year: The season, which the stored draft document does not carry.
            **overrides: Any DraftConfig field to set explicitly, for example
                `random_seed=1`. These win over whatever the document said.

        Returns:
            DraftConfig: A validated config ready to hand to the simulator.

        Raises:
            KeyError: If the document is missing one of the required fields.
            ValueError: If the resulting league is impossible, raised by
                `__post_init__`; or if this draft has keepers left over from the
                older storage shape that still need a team and a round. Refusing
                is deliberate: an unassigned keeper would otherwise be treated as
                a draftable player, and the whole simulation would quietly
                describe a league where somebody else can take him.

        Note:
            Tolerates drafts saved BEFORE starting_slots/keepers/roster_size
            existed by falling back to defaults, so old saved drafts keep working
            rather than raising a KeyError.
        """
        has_keepers = bool(doc.get("has_keepers", bool(doc.get("keepers"))))
        keepers, unassigned = normalize_keepers(doc.get("keepers"))

        if has_keepers and unassigned:
            raise ValueError(
                f"{len(unassigned)} keeper(s) in this draft still need a team and "
                f"a round. Open the Draft Manager page and assign them before "
                f"simulating."
            )

        values = {
            "year": year,
            "num_teams": doc["num_teams"],
            "num_rounds": doc["num_rounds"],
            "draft_position": doc["draft_position"],
            "scoring_format": ScoringFormat(doc["scoring_format"]),
            "platform": doc.get("platform", "espn"),
            "starting_slots": doc.get("starting_slots") or dict(DEFAULT_STARTING_SLOTS),
            "keepers": keepers if has_keepers else (),
            "roster_size": doc.get("roster_size"),
            "third_round_reversal": bool(doc.get("third_round_reversal", False)),
        }
        values.update(overrides)
        return cls(**values)

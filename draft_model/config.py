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
        keepers: Player ids removed from the pool before simulating. Empty for
            redraft leagues.
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

        Cheap, and it turns a confusing downstream failure (an empty simulation,
        an index error a hundred lines away) into an obvious message here.
        """
        if self.num_teams < 2:
            raise ValueError(f"num_teams must be at least 2, got {self.num_teams}")
        if self.num_rounds < 1:
            raise ValueError(f"num_rounds must be at least 1, got {self.num_rounds}")
        if not 1 <= self.draft_position <= self.num_teams:
            raise ValueError(
                f"draft_position {self.draft_position} outside 1..{self.num_teams}"
            )

    @property
    def total_picks(self) -> int:
        """Selections made in the whole draft."""
        return self.num_teams * self.num_rounds

    @cached_property
    def my_picks(self) -> tuple:
        """
        Purpose: The absolute pick numbers you own, ascending.

        Returns:
            tuple[int]: One pick per round, e.g. (5, 20, 29, ...) for slot 5 in a
                12-team draft.

        Notes:
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
        """
        Purpose: A short hash of every input the SIMULATION depends on.

        Returns:
            str: 12 hex characters.

        Notes:
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
        """
        Purpose: Build a config from a saved draft document.

        Parameters:
            doc (dict): A draft from DraftService -- num_teams, draft_position,
                num_rounds, platform, scoring_format (stored as its .value
                string), and optionally the newer starting_slots / keepers /
                roster_size fields.
            year (int): Season, which the draft doc does not carry.
            **overrides: Any DraftConfig field, e.g. random_seed=1.

        Returns:
            DraftConfig.

        Notes:
            Tolerates drafts saved BEFORE starting_slots/keepers/roster_size
            existed by falling back to defaults, so old saved drafts keep working
            rather than raising a KeyError.
        """
        values = {
            "year": year,
            "num_teams": doc["num_teams"],
            "num_rounds": doc["num_rounds"],
            "draft_position": doc["draft_position"],
            "scoring_format": ScoringFormat(doc["scoring_format"]),
            "platform": doc.get("platform", "espn"),
            "starting_slots": doc.get("starting_slots") or dict(DEFAULT_STARTING_SLOTS),
            "keepers": tuple(doc.get("keepers") or ()),
            "roster_size": doc.get("roster_size"),
            "third_round_reversal": bool(doc.get("third_round_reversal", False)),
        }
        values.update(overrides)
        return cls(**values)

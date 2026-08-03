"""Saving and loading simulation results.

A picks matrix on its own is meaningless. picks[:, 47] is a column of numbers
with no indication of WHICH PLAYER it describes -- and the ordering came from a
table that was sorted by ADP at a particular moment, from a particular data pull.
Load it a week later against a re-sorted table and every column silently refers
to the wrong player.

So the player id list is stored WITH the matrix, and a file missing it is treated
as corrupt rather than loaded hopefully. That is invariant 1 from DESIGN.md,
enforced rather than documented.

Format: .npz (numpy's zipped archive). The matrix is mostly the UNDRAFTED
sentinel, which compresses extremely well -- a 10,000 x 237 int16 matrix is 4.7 MB
raw and a fraction of that on disk.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from draft_model.config import UNDRAFTED

ARTIFACT_VERSION = 1
"""Bumped when the stored layout changes incompatibly. Lets a future loader
recognise an old file and say so, instead of misreading it."""

@dataclass
class SimArtifact:
    """One saved simulation run, with everything needed to interpret it.

    Attributes:
        picks: (n_sims, n_players) int16. picks[s, i] is the pick number player i
            went at in simulation s, or UNDRAFTED.
        player_ids: (n_players,) ids in COLUMN ORDER. Without this the matrix
            cannot be read at all.
        config: The DraftConfig as a plain dict, so the file stays readable
            without importing this codebase.
        mu, sd: Calibrated sampler parameters actually used. Stored because they
            are the run's real inputs -- `adp_target` alone would not let you
            reproduce it.
        metadata: Everything else worth knowing later -- seed, rho, timestamps,
            calibration trace, data provenance.
    """

    picks: np.ndarray
    player_ids: np.ndarray
    config: dict
    mu: np.ndarray = None
    sd: np.ndarray = None
    metadata: dict = field(default_factory=dict)

    @property
    def n_sims(self) -> int:
        return int(self.picks.shape[0])

    @property
    def n_players(self) -> int:
        return int(self.picks.shape[1])

    def column_for(self, player_id) -> int:
        """
        Purpose: Find the matrix column belonging to a player.

        Parameters:
            player_id: An id as stored in player_ids.

        Returns:
            int: Column index into picks.

        Raises:
            KeyError: If the player isn't in this run -- e.g. he was beyond the
                pool cap. Better than returning something plausible.

        Notes:
            THE ONLY correct way to index the matrix by player. Never assume the
            ordering matches a table you happen to have in hand; that table may
            have been rebuilt from a newer data pull.
        """
        matches = np.flatnonzero(self.player_ids == player_id)
        if matches.size == 0:
            raise KeyError(f"player {player_id!r} is not in this simulation")
        return int(matches[0])

def artifact_path(directory, draft_id: str, config) -> Path:
    """
    Purpose: Where a run for this draft and these settings belongs on disk.

    Parameters:
        directory (str | Path): Usually data/sim/.
        draft_id (str): The draft this run is for.
        config (DraftConfig): Supplies the fingerprint.

    Returns:
        Path: e.g. data/sim/abc123_3c402ed180a5.npz

    Notes:
        The fingerprint in the filename is what stops a settings change from
        silently reusing a stale run. Change the draft position, get a different
        filename, get a cache miss, recompute -- rather than confidently serving
        numbers computed for a different league.
    """
    return Path(directory) / f"{draft_id}_{config.fingerprint()}.npz"

def save_picks_matrix(path, picks, config, player_ids, *, mu=None, sd=None,
                      metadata=None) -> Path:
    """
    Purpose: Write a simulation run plus everything needed to interpret it.

    Parameters:
        path (str | Path): Destination .npz. Parent directories are created.
        picks (np.ndarray): (n_sims, n_players) matrix from monte_carlo_sim.
        config (DraftConfig): The league this was run for.
        player_ids (sequence): Player ids in COLUMN ORDER -- normally
            table["ffc_player_id"].
        mu, sd (np.ndarray | None): Calibrated parameters used for the run.
        metadata (dict | None): Anything else -- calibration trace, FFC pull date,
            platform weights.

    Returns:
        Path: Where it was written.

    Raises:
        ValueError: If player_ids doesn't have exactly one entry per column. A
            mismatch here is unrecoverable later, so it is refused at write time.

    Notes:
        Config is stored as a plain dict rather than a pickled object. A pickle
        of a dataclass breaks when the class changes; a dict of numbers is
        readable in ten years with `np.load` and nothing else installed.
    """
    picks = np.asarray(picks)
    player_ids = np.asarray(player_ids)

    if player_ids.shape[0] != picks.shape[1]:
        raise ValueError(
            f"player_ids has {player_ids.shape[0]} entries but picks has "
            f"{picks.shape[1]} columns -- the matrix would be uninterpretable"
        )

    config_dict = {
        "year": config.year,
        "num_teams": config.num_teams,
        "num_rounds": config.num_rounds,
        "draft_position": config.draft_position,
        "scoring_format": config.scoring_format.value,
        "starting_slots": dict(config.starting_slots),
        "keepers": list(config.keepers),
        "roster_size": config.roster_size,
        "third_round_reversal": config.third_round_reversal,
        "random_seed": config.random_seed,
        "fingerprint": config.fingerprint(),
        "my_picks": list(config.my_picks),
    }

    full_metadata = {
        "artifact_version": ARTIFACT_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "n_sims": int(picks.shape[0]),
        "n_players": int(picks.shape[1]),
        "undrafted_sentinel": UNDRAFTED,
        **(metadata or {}),
    }

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    arrays = {
        "picks": picks.astype(np.int16),
        # Ids may be numeric or string depending on source; store as text so one
        # loader handles both without guessing.
        "player_ids": player_ids.astype(str),
        "config_json": np.array(json.dumps(config_dict)),
        "metadata_json": np.array(json.dumps(full_metadata, default=str)),
    }
    if mu is not None:
        arrays["mu"] = np.asarray(mu, dtype=np.float64)
    if sd is not None:
        arrays["sd"] = np.asarray(sd, dtype=np.float64)

    np.savez_compressed(path, **arrays)
    return path

def load_picks_matrix(path) -> SimArtifact:
    """
    Purpose: Read a saved run back.

    Parameters:
        path (str | Path): Source .npz.

    Returns:
        SimArtifact.

    Raises:
        FileNotFoundError: Path doesn't exist.
        KeyError: The archive is missing `picks` or `player_ids`. The second is
            fatal specifically because the matrix cannot be interpreted without
            it -- refusing to load beats returning columns nobody can identify.

    Notes:
        allow_pickle stays OFF. Everything stored is a plain array or a JSON
        string, so there is no reason to enable arbitrary code execution on load.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no simulation artifact at {path}")

    with np.load(path, allow_pickle=False) as archive:
        for required in ("picks", "player_ids"):
            if required not in archive:
                raise KeyError(
                    f"{path.name} is missing '{required}'. Without the player "
                    f"ordering the matrix cannot be interpreted (invariant 1)."
                )

        return SimArtifact(
            picks=archive["picks"],
            player_ids=archive["player_ids"],
            config=json.loads(str(archive["config_json"])) if "config_json" in archive else {},
            mu=archive["mu"] if "mu" in archive else None,
            sd=archive["sd"] if "sd" in archive else None,
            metadata=json.loads(str(archive["metadata_json"])) if "metadata_json" in archive else {},
        )

def matches_table(artifact: SimArtifact, table) -> bool:
    """
    Purpose: Does a saved run still describe the table in front of you?

    Parameters:
        artifact (SimArtifact): A loaded run.
        table (pd.DataFrame): A freshly built table with ffc_player_id.

    Returns:
        bool: True only if the ids AND their order match exactly.

    Notes:
        The check is on ORDER, not just membership. Same players sorted
        differently is exactly the case that produces confidently wrong answers,
        because every column would refer to the wrong person.

        Use before trusting a cached artifact against a newer data pull. The
        fingerprint in the filename catches SETTINGS changes; this catches DATA
        changes, which the fingerprint knows nothing about.
    """
    current = np.asarray(table["ffc_player_id"]).astype(str)
    return (current.shape == artifact.player_ids.shape
            and bool((current == artifact.player_ids).all()))

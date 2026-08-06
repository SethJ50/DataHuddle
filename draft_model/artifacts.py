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
        """Count how many drafts were simulated in this run.

        A convenience so callers can ask the artifact directly rather than
        remembering which side of the matrix's shape means what.

        Steps:
            1. Read the first number of the picks matrix's shape, which is its
               row count, and return it as a plain integer.

        Returns:
            int: The number of simulated drafts, typically 10,000.
        """
        return int(self.picks.shape[0])

    @property
    def n_players(self) -> int:
        """Count how many players were in the pool for this run.

        Steps:
            1. Read the second number of the picks matrix's shape, which is its
               column count, and return it as a plain integer.

        Returns:
            int: The number of players, which is also the length of
                `player_ids`.
        """
        return int(self.picks.shape[1])

    def column_for(self, player_id) -> int:
        """Find which column of the picks matrix describes a given player.

        The picks matrix has no player labels of its own — column 47 is just a
        column of numbers. This looks the player up in the id list that was saved
        alongside it, which is the only trustworthy way to make that connection.

        Steps:
            1. Search the stored id list for every position matching this player
               id.
            2. If nothing matched, raise rather than returning a plausible-looking
               wrong column.
            3. Return the first match as a plain integer.

        Args:
            player_id: An id exactly as stored in `player_ids`. Ids are saved as
                text, so a numeric id must be compared as its string form.

        Returns:
            int: The column index into `picks` for this player.

        Raises:
            KeyError: If the player is not in this run — for example he was
                beyond the pool cap when the table was built. Better than
                returning something plausible.

        Note:
            THE ONLY correct way to index the matrix by player. Never assume the
            ordering matches a table you happen to have in hand; that table may
            have been rebuilt from a newer data pull.
        """
        matches = np.flatnonzero(self.player_ids == player_id)
        if matches.size == 0:
            raise KeyError(f"player {player_id!r} is not in this simulation")
        return int(matches[0])

def artifact_path(directory, draft_id: str, config) -> Path:
    """Work out the file path where a run for this draft and settings belongs.

    Building the filename from both the draft and the settings fingerprint is
    what makes the on-disk cache safe: different settings simply produce a
    different filename.

    Steps:
        1. Call `config.fingerprint()` for the short hash of every setting the
           simulation depends on.
        2. Join the directory with a filename combining the draft id and that
           fingerprint.

    Args:
        directory: The folder to put it in, usually data/sim/. May be a string or
            a Path.
        draft_id: The identifier of the draft this run is for.
        config: The league settings, which supply the fingerprint.

    Returns:
        Path: The full path, for example data/sim/abc123_3c402ed180a5.npz. This
            only computes the path; nothing is created or written.

    Note:
        The fingerprint in the filename is what stops a settings change from
        silently reusing a stale run. Change the draft position, get a different
        filename, get a cache miss, recompute -- rather than confidently serving
        numbers computed for a different league.
    """
    return Path(directory) / f"{draft_id}_{config.fingerprint()}.npz"

def save_picks_matrix(path, picks, config, player_ids, *, mu=None, sd=None,
                      metadata=None) -> Path:
    """Write a simulation run to disk, along with everything needed to read it back.

    Saving the matrix alone would be useless a week later, so the player ordering,
    the league settings, and the calibrated parameters all go into the same file.

    Steps:
        1. Convert the matrix and the id list to numpy arrays.
        2. Refuse to write if there is not exactly one player id per matrix
           column, since that mismatch is unrecoverable later.
        3. Flatten the config into a plain dictionary of numbers and strings.
        4. Build the metadata, starting with the artifact version, save time, and
           shape, then merging in whatever the caller supplied.
        5. Create the destination folder if it does not exist yet.
        6. Assemble the arrays to store, shrinking the matrix to int16 and
           converting the ids to text so one loader handles numeric and string
           ids alike. Add `mu` and `sd` only if they were supplied.
        7. Write it all out as a compressed .npz archive.

    Args:
        path: The destination .npz file. Parent directories are created for you.
        picks: The (n_sims, n_players) results matrix from `monte_carlo_sim`.
        config: The league settings this was run for.
        player_ids: The player ids in COLUMN ORDER — normally
            `table["ffc_player_id"]`. Order is what makes the matrix readable.
        mu: The calibrated centres actually used for the run.
        sd: The calibrated widths actually used for the run.
        metadata: Anything else worth keeping — the calibration trace, the FFC
            pull date, the platform weights.

    Returns:
        Path: Where the file was written.

    Raises:
        ValueError: If `player_ids` does not have exactly one entry per column. A
            mismatch here is unrecoverable later, so it is refused at write time.

    Note:
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
        # Stored as plain dictionaries rather than Keeper objects so the file
        # stays readable with np.load alone, exactly like the rest of the config.
        "keepers": [
            {"team": k.team, "round": k.round, "canonical_id": k.canonical_id}
            for k in config.keepers
        ],
        "keeper_picks": {str(pick): pid for pick, pid in config.keeper_picks.items()},
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
    """Read a saved simulation run back off disk.

    The counterpart to `save_picks_matrix` above. It refuses to return a
    half-usable artifact: a file without the player ordering is treated as
    corrupt rather than loaded hopefully.

    Steps:
        1. Check the file exists, and raise a clear error if not.
        2. Open the .npz archive with pickle loading disabled — see the note.
        3. Confirm both `picks` and `player_ids` are present, raising if either
           is missing.
        4. Build the SimArtifact, parsing the config and metadata back from their
           stored JSON text and treating `mu` and `sd` as optional.

    Args:
        path: The .npz file to read, as a string or Path.

    Returns:
        SimArtifact: The loaded run, with the matrix, the player ordering, the
            config, the calibrated parameters, and the metadata.

    Raises:
        FileNotFoundError: If no file exists at that path.
        KeyError: If the archive is missing `picks` or `player_ids`. The second
            is fatal specifically because the matrix cannot be interpreted
            without it — refusing to load beats returning columns nobody can
            identify.

    Note:
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
    """Check whether a saved run still describes the player table in front of you.

    The guard against serving a cached simulation whose columns no longer line up
    with the current data. Call it before trusting a loaded artifact.

    Steps:
        1. Take the table's `ffc_player_id` column and convert it to text, since
           the artifact stores its ids that way.
        2. Confirm both lists are the same length.
        3. Confirm every id matches position by position, which is what checks
           the ORDER rather than just the membership.

    Args:
        artifact: A run loaded by `load_picks_matrix` above.
        table: A freshly built player table containing `ffc_player_id`.

    Returns:
        bool: True only if the ids AND their order match exactly. False means the
            artifact must be regenerated rather than reused.

    Note:
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

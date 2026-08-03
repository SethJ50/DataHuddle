"""Builds the single flat table the whole model runs on.

This is the boundary between messy vendor data and clean model input. Nothing
downstream ever touches a vendor field directly -- the simulator sees only
`mu`, `sd` and `position`, and every judgment about how those were derived is
made here, once, visibly.

Pure pandas on purpose: no Mongo, no services, no Streamlit. Everything arrives
as a DataFrame or Series, which is what makes it testable with fixtures instead
of a live database.

THE INVARIANT THAT MATTERS MOST (DESIGN.md, invariant 1):
    The row order of this table IS the column order of the picks matrix.
    table.iloc[i] describes picks[:, i], always. Never sort, filter or reindex
    the table after a simulation without regenerating the matrix.
"""

import numpy as np
import pandas as pd

from draft_model.config import MIN_STDEV, PLATFORM_WEIGHT, POOL_MULTIPLIER

REQUIRED_FFC_COLUMNS = ("ffc_player_id", "name", "position", "adp", "stdev", "high", "low")

def blend_adp(sources: dict, weights: dict) -> pd.Series:
    """
    Purpose: Combine ADP from several platforms into one consensus number.

    Parameters:
        sources (dict[str, pd.Series]): Platform name -> ADP, each Series indexed
            by canonical_id. They need NOT cover the same players.
        weights (dict[str, float]): Platform name -> relative weight. These do not
            need to sum to 1.

    Returns:
        pd.Series: Blended ADP indexed by canonical_id, covering the union of all
            input indices. NaN for a player no source has.

    Notes:
        Exists as a named function specifically so the weighting is a visible,
        reviewable decision rather than a magic line buried inside build_table.

        WEIGHTS ARE RENORMALIZED PER PLAYER, which is the subtle part. If Sleeper
        has weight 0.2 and a deep player appears ONLY in Sleeper, he should get
        Sleeper's ADP -- not Sleeper's ADP scaled down to a fifth of it. Dividing
        by the weight actually present fixes that; a plain weighted sum does not.

        Weight the platform you actually draft on most heavily. The default list a
        platform shows in-app anchors your real leaguemates far more strongly than
        any consensus ranking does.
    """
    if not sources:
        return pd.Series(dtype="float64")

    frame = pd.DataFrame(sources)                       # index = union of players
    weight_row = pd.Series(weights).reindex(frame.columns).fillna(0.0)

    # Only count a platform's weight where it actually has a number for that player.
    present = frame.notna()
    applied = present.mul(weight_row, axis=1)
    total_weight = applied.sum(axis=1)

    weighted_sum = frame.fillna(0.0).mul(applied).sum(axis=1)

    # total_weight of 0 means no source had him -> NaN, not a divide-by-zero.
    return weighted_sum / total_weight.replace(0.0, np.nan)

def apply_platform_shift(ffc_adp: pd.Series, platform_adp: pd.Series,
                         weight: float = PLATFORM_WEIGHT) -> pd.Series:
    """
    Purpose: Nudge FFC's ADP toward the platform you actually draft on.

    Parameters:
        ffc_adp (pd.Series): FFC ADP, indexed however the caller likes.
        platform_adp (pd.Series): Blended platform ADP on the SAME index.
        weight (float): 0.0 keeps pure FFC; 1.0 goes all the way to the platform.

    Returns:
        pd.Series: Shifted ADP on the same index as ffc_adp.

    Notes:
        adp_target = ffc_adp + weight * (platform_adp - ffc_adp)

        Why a shift instead of just using platform ADP: FFC is the only source of
        `stdev`, and its spread describes variation within FFC's own drafts around
        FFC's own ADP. Pairing that width with a completely different centre mixes
        two populations. Shifting keeps one coherent base and makes the platform
        anchor an explicit, tunable adjustment instead of a silent mismatch.

        Players missing from either side keep their FFC value untouched. Only the
        CENTRE moves -- there is no defensible reason for this to touch the width.
    """
    if platform_adp is None or platform_adp.empty or weight == 0.0:
        return ffc_adp.copy()

    aligned = platform_adp.reindex(ffc_adp.index)
    shift = (aligned - ffc_adp) * weight
    return ffc_adp + shift.fillna(0.0)

def fill_missing_stdev(df: pd.DataFrame, adp_column: str = "adp_target",
                       n_neighbors: int = 20) -> pd.Series:
    """
    Purpose: Give every player a usable width, without any trained model.

    Parameters:
        df (pd.DataFrame): Needs columns position, stdev, high, low, and adp_column.
            `stdev` is NaN where FFC could not measure a spread (the adapter turns
            FFC's meaningless 0 into NaN).
        adp_column (str): Which ADP defines "nearby" -- the shifted target.
        n_neighbors (int): How many same-position neighbours to average over.

    Returns:
        pd.Series: A width for every row, aligned to df.index, guaranteed > 0.

    Notes:
        THE FALLBACK CHAIN, in order:
          1. FFC's stdev, whenever present. The overwhelming majority -- measured
             on the 2026 pull, only 1 of 246 PPR players needs anything else.
          2. (high - low) / 4. For a roughly bell-shaped spread the range spans
             about four standard deviations. Crude, and it overreacts to a single
             drafter who reached, but calibration absorbs much of the error.
          3. Median stdev of the nearest-ADP players AT THE SAME POSITION.

        Why step 3 is a fair estimate rather than a guess: spread rises smoothly
        and predictably with ADP (median 2.65 -> 6.0 -> 10.35 -> 15.15 across ADP
        bands in 2026). Same-position because QB and TE spread differently from
        RB and WR -- and every position has at least 19 usable players, so the
        neighbourhood is never thin.

        DELIBERATELY NOT A MODEL. No training, no saved artifact, no historical
        data -- computed fresh from the table in front of it. The pipeline is
        meant to have no dependency on a fitted width model (DESIGN.md 5.5).

        Thin-sample players are LEFT ALONE. A player drafted 5 times has a noisy
        stdev, but it is a real observation of real drafts, and calibration
        adjusts `sd` anyway. Smoothing them was tried in an earlier design and
        dropped as unnecessary complexity.
    """
    stdev = pd.to_numeric(df["stdev"], errors="coerce").astype("float64").copy()

    # --- step 2: derive from the observed high/low range ---
    spread = (pd.to_numeric(df["low"], errors="coerce")
              - pd.to_numeric(df["high"], errors="coerce")).abs() / 4.0
    from_range = stdev.isna() & spread.gt(0)
    stdev.loc[from_range] = spread[from_range]

    # --- step 3: same-position ADP neighbourhood ---
    # Snapshot which rows are usable BEFORE filling, so the result doesn't depend
    # on the order we happen to iterate in.
    usable = stdev.notna()
    for idx in df.index[stdev.isna()]:
        pool = df.index[usable & (df["position"] == df.at[idx, "position"])]
        if len(pool) == 0:
            pool = df.index[usable]          # no same-position data at all
        if len(pool) == 0:
            continue                          # nothing anywhere; floor catches it

        distance = (df.loc[pool, adp_column] - df.at[idx, adp_column]).abs()
        nearest = distance.nsmallest(min(n_neighbors, len(pool))).index
        stdev.at[idx] = stdev.loc[nearest].median()

    # Final floor. The chain above should never produce a zero; this makes sure a
    # failure is bounded rather than silently making someone deterministic.
    return stdev.fillna(MIN_STDEV).clip(lower=MIN_STDEV)

def build_table(config, ffc: pd.DataFrame, platform_adp: pd.Series = None,
                enrichments: dict = None, platform_weight: float = PLATFORM_WEIGHT,
                pool_multiplier: float = POOL_MULTIPLIER) -> pd.DataFrame:
    """
    Purpose: Assemble the one flat table the entire model runs on.

    Parameters:
        config (DraftConfig): League settings; total_picks sets the pool cap.
        ffc (pd.DataFrame): One row per player from FfcService.with_canonical_id --
            ffc_player_id, name, position, team, adp, stdev, high, low,
            times_drafted, bye, canonical_id (nullable).
        platform_adp (pd.Series | None): Blended platform ADP indexed by
            canonical_id, from blend_adp. None keeps pure FFC.
        enrichments (dict[str, pd.Series] | None): Extra columns to attach by
            canonical_id, e.g. {"projection": ..., "upside": ..., "risk": ...}.
            Missing players get NaN; nothing is dropped for lacking them.
        platform_weight (float): Passed to apply_platform_shift.
        pool_multiplier (float): Drop players beyond total_picks * this.

    Returns:
        pd.DataFrame indexed 0..n-1 (THIS INDEX DEFINES PICKS-MATRIX COLUMN
        ORDER), sorted by adp_target, with columns:
            ffc_player_id  int    the sim's key -- see note below
            canonical_id   str    NULLABLE; display layer only
            name, position, team
            adp_target     float  centre the simulation must reproduce
            stdev_target   float  width, guaranteed > 0
            times_drafted  float  sample size behind the FFC numbers
            mu, sd         float  sampler parameters
            ...plus any enrichment columns
        `mu`/`sd` start equal to adp_target/stdev_target and are MEANINGLESS
        until calibrate_sampler has run and written back to them.

    Raises:
        ValueError: If a required column is missing, or any surviving row has no
            adp or no position. Silent NaNs here become NaN board values, which
            sort unpredictably and produce a plausible-looking wrong draft.

    Notes:
        KEYED BY ffc_player_id, NOT canonical_id. The simulator needs only adp,
        stdev and position -- it does not need identity resolution. Team defenses
        can never resolve to an nflreadpy id, and dropping them would push ~27
        skill players artificially later, since defenses really do come off the
        board in real drafts. So canonical_id rides along as a nullable join
        column for the display layer, and is never used as a filter.

        KEEPERS ARE NOT REMOVED HERE. The table is the full universe; removal
        happens per-simulation, so one table serves both keeper and redraft cases.
    """
    missing = [c for c in REQUIRED_FFC_COLUMNS if c not in ffc.columns]
    if missing:
        raise ValueError(f"ffc table is missing required columns: {missing}")

    table = ffc.copy()

    # --- centre: FFC ADP nudged toward the platform you actually draft on ---
    if platform_adp is not None and "canonical_id" in table.columns:
        # Move onto canonical_id so the two can be aligned, then back again.
        platform_for_rows = pd.Series(
            table["canonical_id"].map(platform_adp).values,
            index=table.index, dtype="float64",
        )
        table["adp_target"] = apply_platform_shift(
            table["adp"].astype("float64"), platform_for_rows, platform_weight
        )
    else:
        table["adp_target"] = table["adp"].astype("float64")

    # --- fail loudly on holes, BEFORE anything can quietly discard them ---
    # Order matters here. The pool cap below is a comparison, and in pandas
    # `NaN <= cap` is False -- so a player with no ADP would be silently dropped
    # by the cap rather than reported, which is precisely the silent hole this
    # check exists to catch. Validate first, filter second.
    for column in ("adp_target", "position"):
        if table[column].isna().any():
            bad = table.loc[table[column].isna(), ["name", "position", "adp"]]
            raise ValueError(f"{column} is missing for {len(bad)} players:\n{bad}")

    # --- width: the model-free fallback chain ---
    # Run BEFORE the pool cap so deep players still have a full neighbourhood of
    # same-position comparables to draw a median from.
    table["stdev_target"] = fill_missing_stdev(table)

    if table["stdev_target"].isna().any():
        bad = table.loc[table["stdev_target"].isna(), ["name", "position", "adp"]]
        raise ValueError(f"stdev_target is missing for {len(bad)} players:\n{bad}")

    # --- pool cap ---
    # A player with ADP 400 in a 180-pick draft can never be selected, and costs
    # exactly as much to simulate as anyone else.
    cap = config.total_picks * pool_multiplier
    table = table[table["adp_target"] <= cap]

    # --- optional enrichment, joined by canonical_id, never a filter ---
    for name, series in (enrichments or {}).items():
        if "canonical_id" in table.columns:
            table[name] = table["canonical_id"].map(series)
        else:
            table[name] = np.nan

    # --- sampler parameters start at the raw targets ---
    # Meaningless until calibrate_sampler overwrites them (invariant 4).
    table["mu"] = table["adp_target"]
    table["sd"] = table["stdev_target"]

    # Sorting happens ONCE, here, and the resulting order is frozen for the life
    # of the simulation artifact (invariant 1).
    ordered = ["ffc_player_id", "canonical_id", "name", "position", "team",
               "adp_target", "stdev_target", "times_drafted", "mu", "sd"]
    ordered += [c for c in (enrichments or {})]
    ordered = [c for c in ordered if c in table.columns]

    return table.sort_values("adp_target")[ordered].reset_index(drop=True)

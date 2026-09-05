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
    """Combine ADP from several platforms into one consensus number per player.

    Different platforms disagree about when a player goes, and no single one is
    authoritative. This averages them, letting you weight the platform you
    actually draft on more heavily.

    Steps:
        1. Return an empty result if no sources were given.
        2. Build a table from the sources: one column per platform, one row per
           player, covering every player any platform mentions. Players a
           platform has no number for come out blank in that column.
        3. Line the weights up with those columns, treating a platform with no
           listed weight as weight 0.
        4. Work out which entries are actually present, and total up only the
           weights that apply to each player. This is the renormalization step
           described in the note below.
        5. Multiply each present value by its weight and sum across platforms.
        6. Divide by the applicable weight. A total of 0 means no source had the
           player, so it is turned into NaN first to avoid dividing by zero.

    Args:
        sources: Maps a platform name to its ADP values, each labelled by
            canonical player id. The platforms need NOT cover the same players.
        weights: Maps a platform name to its relative importance. These do not
            need to add up to 1, since step 6 divides by whatever is present.

    Returns:
        pd.Series: Blended ADP labelled by canonical id, covering every player
            any source mentioned. NaN for a player no source has.

    Note:
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

def impute_missing_shift(gap: pd.Series, reference_adp: pd.Series,
                         n_neighbors: int = 20) -> pd.Series:
    """Estimate the FFC-to-platform gap for players no platform ranks.

    "Gap" here means how many picks later the platforms draft a player than FFC
    does. It can only be measured for players both sources cover. This fills in
    the rest by asking what the gap looks like for the players drafted around
    them, so an unranked player does not get left behind on a different scale
    while everyone near him moves.

    Steps:
        1. Split the players into those with a measured gap and those without.
        2. If nobody has a measured gap, hand back the input unchanged -- there
           is nothing to learn from.
        3. For each player missing one, measure how far every player WITH a
           measured gap sits from him in ADP.
        4. Take the closest `n_neighbors` of those and use the median of their
           gaps, which ignores a single wild disagreement rather than being
           dragged by it.

    Args:
        gap: Platform ADP minus FFC ADP, one entry per player, NaN wherever no
            platform ranks him.
        reference_adp: The ADP that defines "drafted around him" for step 3,
            normally FFC's, on the same labels as `gap`.
        n_neighbors: How many nearby measured players to take the median over.

    Returns:
        pd.Series: The same gaps with the NaN entries filled in. Still NaN only
            in the case where no player anywhere had a measured gap.

    Note:
        DELIBERATELY NOT SAME-POSITION, unlike `fill_missing_stdev` below. The
        players who need this are overwhelmingly kickers and defenses, and
        NEITHER position has a single measured gap to learn from -- ESPN and
        Sleeper publish no K or DST ADP at all, and Yahoo's is drawn from a
        different draft shape entirely (it puts the top kicker around pick 85
        against FFC's 133). A same-position neighbourhood would be empty for
        precisely the players this exists to serve.

        WHAT THIS IS AND IS NOT CLAIMING. It does not pretend to know where the
        platforms would rank Denver's defense. It claims only that the two
        sources describe differently-shaped drafts, that the difference varies
        smoothly with depth, and that an unranked player should ride that shape
        change rather than sit still while his neighbours move. Measured on the
        2026 half-PPR pull, the imputed gap decays smoothly from about +19 picks
        at ADP 95 to about -5 at ADP 180, so there is a real, stable local
        signal here rather than noise.

        Without this, raising PLATFORM_WEIGHT silently distorts the late rounds:
        36 of 205 players (all 16 defenses, all 17 kickers, 3 receivers) would
        hold FFC's ADP while every skill player around them moved, and the
        higher the weight the worse the split.
    """
    measured = gap.notna()
    if not measured.any():
        return gap

    filled = gap.copy()
    measured_labels = gap.index[measured]

    for label in gap.index[~measured]:
        distance = (reference_adp.loc[measured_labels] - reference_adp.loc[label]).abs()
        nearest = distance.nsmallest(min(n_neighbors, len(measured_labels))).index
        filled.loc[label] = gap.loc[nearest].median()

    return filled


def rank_to_pick_scale(rank: pd.Series, reference_adp: pd.Series) -> pd.Series:
    """Turn a platform's ordinal board rank into pick numbers.

    A board rank says only who is ahead of whom -- 1st, 2nd, 3rd -- with no
    notion of how far apart they are. ADP is measured in picks. Averaging the
    two directly would be meaningless, so this restates the rank in pick units
    by borrowing the spacing from a real ADP column.

    Steps:
        1. Return an empty result if there is nothing to convert.
        2. Dense-rank the players, turning whatever sparse numbers the source
           published into a clean 1, 2, 3, ... within this pool.
        3. Sort the reference ADP values, smallest first.
        4. Hand the player ranked Nth the Nth smallest ADP value, so the top of
           the board gets the earliest pick numbers.
        5. Clamp anyone ranked deeper than the reference list onto its last
           value, rather than running off the end.

    Args:
        rank: The source's board position per player, lower being better. Need
            not be dense or start at 1.
        reference_adp: Real ADP values whose SPACING should be borrowed, on the
            same players. Normally the same source's own ADP column.

    Returns:
        pd.Series: A pick number per player, labelled like `rank`.

    Note:
        The output is a PERMUTATION of `reference_adp` -- the same set of
        numbers, redistributed by board order. That is the useful property: it
        can only ever re-order the board, never stretch or shift its scale, so
        it cannot fight `fit_to_pick_space` below and cannot move the average
        pick at all.

        Borrowing the spacing matters. Real ADP is packed tightly at the top
        (picks 1, 2, 3 are barely apart) and spreads out deep, and handing out a
        flat 1..N instead would make early players look far more interchangeable
        than they are.

        Dense-ranking in step 2 is what handles Yahoo's sparse ranks, which run
        to 2473 across 1,175 players. Using them raw would place a kicker
        thousands of picks deep.
    """
    if len(rank) == 0 or len(reference_adp.dropna()) == 0:
        return pd.Series(dtype="float64")

    # "first" breaks ties by order of appearance, so two players never share a
    # slot -- the caller is going to average this, and a tie would double up.
    dense = rank.rank(method="first").to_numpy()
    values = np.sort(reference_adp.dropna().to_numpy())

    positions = np.clip(dense.astype(int) - 1, 0, len(values) - 1)
    return pd.Series(values[positions], index=rank.index, dtype="float64")


def apply_platform_shift(ffc_adp: pd.Series, platform_adp: pd.Series,
                         weight: float = PLATFORM_WEIGHT,
                         impute_missing: bool = True) -> pd.Series:
    """Nudge FFC's ADP part of the way toward the platform you actually draft on.

    Your leaguemates see your platform's default player list, so it predicts
    their behavior better than any consensus ranking. But FFC is the only source
    of spread, so its centre cannot simply be thrown away — hence a partial
    shift rather than a replacement.

    Steps:
        1. If there is no platform data, or the weight is zero, hand back a copy
           of the FFC values untouched.
        2. Line the platform values up with the FFC ones, so both are labelled
           the same way.
        3. Work out the gap between them.
        4. Unless told otherwise, fill in the gap for players no platform ranks
           with `impute_missing_shift` above, so they move with the players
           around them instead of being left on FFC's scale.
        5. Scale whatever gap each player ended up with by the weight and add it
           to his FFC value.

    Args:
        ffc_adp: FFC's ADP values, labelled however the caller likes.
        platform_adp: Blended platform ADP on the SAME labels.
        weight: How far to move. 0.0 keeps pure FFC; 1.0 goes all the way to the
            platform; 0.75 is the default set in draft_model/config.py.
        impute_missing: When True, players no platform ranks are moved by the
            typical shift of their ADP neighbours. Set False to leave them
            exactly where FFC put them, which is what this did before the
            imputation existed.

    Returns:
        pd.Series: Shifted ADP with the same labels as `ffc_adp`.

    Note:
        adp_target = ffc_adp + weight * (platform_adp - ffc_adp)

        Why a shift instead of just using platform ADP: FFC is the only source of
        `stdev`, and its spread describes variation within FFC's own drafts around
        FFC's own ADP. Pairing that width with a completely different centre mixes
        two populations. Shifting keeps one coherent base and makes the platform
        anchor an explicit, tunable adjustment instead of a silent mismatch.

        THE MISMATCH THIS DOES NOT FIX, and which grows with `weight`: only the
        centre moves. `stdev_target` stays FFC-scale, and spread rises steeply
        with ADP (median 2.65 -> 15.15 across ADP bands). A player pulled 15
        picks earlier therefore keeps a width belonging to where he used to sit.
        At 0.75 that is a bounded cost; it is the main thing standing in the way
        of going to 1.0.

        Only the CENTRE moves -- there is no defensible reason for this to touch
        the width DIRECTLY. The point above is about the width being left stale,
        not about this function editing it.
    """
    if platform_adp is None or platform_adp.empty or weight == 0.0:
        return ffc_adp.copy()

    aligned = platform_adp.reindex(ffc_adp.index)
    gap = aligned - ffc_adp

    # Keep unranked players on the same scale as their neighbours rather than
    # anchored to FFC while everyone around them moves -- see the note there.
    if impute_missing:
        gap = impute_missing_shift(gap, ffc_adp)

    return ffc_adp + (gap * weight).fillna(0.0)

def adjust_for_keepers(adp_target: pd.Series, keeper_picks: dict = None,
                       kept=None, iterations: int = 3) -> pd.Series:
    """Restate vendor ADP as when a player goes in YOUR keeper league.

    Vendor ADP is measured in redraft drafts, where every player is available.
    In a keeper league he is not: the kept players never reach the board, so
    everyone else really does go earlier than the vendor says. Comparing a
    keeper league's simulation against raw vendor ADP therefore measures the
    league's rules, not the model's accuracy. This restates the target so the
    comparison is fair.

    Steps:
        1. Return the targets untouched if this is a redraft league.
        2. Collect the vendor ADP of every kept player, and the overall pick
           numbers their teams spend on them.
        3. For each player, count the kept players going EARLIER than him. Each
           one vacates a slot he moves up into, so subtract that count.
        4. Count the keeper picks landing before his new position. Each one is a
           pick where nobody is selected, so add that count back.
        5. Repeat step 4 a few times, since moving a player can change how many
           keeper picks now sit before him. It settles almost immediately.

    Args:
        adp_target: Vendor-derived centre for every player.
        keeper_picks: Maps an overall pick number to the kept player, as
            `DraftConfig.keeper_picks` returns. None or empty means redraft.
        kept: One flag per player, True where he is being kept, aligned with
            `adp_target`.
        iterations: How many times to re-count step 4. Three is comfortably
            more than needed; the count can only move by the number of keepers.

    Returns:
        pd.Series: Adjusted centres on the same labels. Identical to the input
            for a redraft league.

    Note:
        WORKED EXAMPLE, the one this was built from. Ja'Marr Chase has an ADP of
        pick 3 and is kept at 3.01, which in a 12-team league is overall pick 25.

            picks 1-2    unaffected -- he was not going that early anyway
            picks 3-24   everyone moves up ONE, since Chase is not there to take
            pick 25      consumed by Chase; nobody is selected
            picks 26+    unchanged -- the keeper pick absorbed the shift

        So the window is exactly "his ADP through his keeper pick", one slot.

        IT WORKS IN BOTH DIRECTIONS. A keeper held LATER than his ADP (kept in
        round 1 with an ADP of 50) pushes players the other way: his pick is
        consumed early while he is removed from deeper in the pool, so the
        players between land one slot LATER. The same two counts handle it with
        no special case.

        WHY THIS MATTERS MORE THAN IT LOOKS. Before this existed, the 12-keeper
        league scored 5.24 picks of "error" against a tolerance of 2.0, and the
        only way to make it save was to weaken its platform weight to 0.6. Most
        of that error was the answer key, not the model.

        Kept players are adjusted too, by the same formula. Their own targets
        are meaningless -- they go at a fixed pick -- but every scoring path
        already excludes them, and adjusting everyone keeps the board on one
        scale rather than leaving a dozen players on a different one.
    """
    keeper_picks = keeper_picks or {}
    if not keeper_picks or kept is None:
        return adp_target

    values = adp_target.to_numpy(dtype=float)
    kept_mask = np.asarray(kept, dtype=bool)

    # Where the kept players would have gone, and which picks they consume.
    kept_targets = np.sort(values[kept_mask])
    consumed_picks = np.sort(np.array(sorted(keeper_picks), dtype=float))
    if len(kept_targets) == 0:
        return adp_target

    # Each kept player going earlier frees a slot this player moves up into.
    # side="left" counts STRICTLY earlier, so a kept player never counts himself.
    vacated = np.searchsorted(kept_targets, values, side="left")

    adjusted = values - vacated
    for _ in range(iterations):
        # Each keeper pick before him is a pick where nobody gets selected.
        blocked = np.searchsorted(consumed_picks, adjusted, side="left")
        adjusted = values - vacated + blocked

    return pd.Series(adjusted, index=adp_target.index)


def fit_to_pick_space(adp_target: pd.Series, total_picks: int,
                      keeper_picks: dict = None, kept=None) -> pd.Series:
    """Rescale ADP targets so a draft this size can actually produce them.

    A draft hands out each of its pick numbers exactly once, so the average pick
    of everyone selected is fixed by arithmetic before the draft even starts. If
    the players who will be selected carry targets averaging later than that, no
    simulation can hit them: it is forced to draft everybody early. This squeezes
    the targets so their average lands where the draft can actually put it.

    Steps:
        1. Work out which pick numbers are spent on real SELECTIONS, which means
           all of them in a redraft league and everything except the keeper picks
           in a keeper league.
        2. Set aside the kept players, who are never selected by anybody.
        3. Take as many of the earliest remaining targets as there are selections
           to make -- near enough the players the draft will consume.
        4. Compare their average against the average selection pick number, and
           divide one by the other to get a single scale factor.
        5. Multiply every target by it, kept players included, so the whole board
           stays on one scale.
        6. If there are fewer candidates than selections, or the average is zero,
           hand the targets back untouched rather than scaling by a meaningless
           factor.

    Args:
        adp_target: The centre for every player, after any platform shift.
        total_picks: How many picks this draft makes in total, which is teams
            times rounds. NOT the same as the number of selections when there
            are keepers.
        keeper_picks: Maps an overall pick number to the player kept with it, as
            `DraftConfig.keeper_picks` returns. Only the pick numbers are read.
            None or empty means a redraft league.
        kept: One flag per player, True where another team is keeping him,
            aligned with `adp_target`. Those players are excluded from the
            average because they never compete for a selection.

    Returns:
        pd.Series: The same targets, multiplied by one shared number, on the
            same labels. Ordering is untouched, and so is the RELATIVE spacing
            between players.

    Note:
        WHY THIS IS NEEDED, and why it is not a fudge. Measured on ESPN Fantasy
        Freaks (170 picks) with the 2026 pull, the average target of the 170
        players who get drafted was:

            pure FFC              81.93   ->  forced +3.57 picks LATE
            half-and-half         85.76   ->  forced -0.26 picks (neutral)
            pure platform blend   88.91   ->  forced -3.41 picks EARLY

        The platforms spread players deeper than a 170-pick draft can express,
        so aiming at them directly forces the simulation to take EVERYONE early
        -- which is exactly the uniformly-negative error the model showed, and
        which calibration provably cannot remove because it is arithmetic
        rather than aim. Correcting it is what lets `platform_weight` be raised
        at all.

        It also explains why 0.5 used to look like a well-chosen value. It was
        not tuned -- it simply happened to be the weight where the two scales
        cancelled, which is a coincidence of this data and would drift the next
        time either source moved.

        ONE shared multiplier, deliberately. Anything per-player would be
        re-ranking the board, which is the platforms' job, not this function's.

        KEEPERS HAVE TO BE HANDLED HERE, and getting it wrong is expensive.
        Measured on a 12-team league keeping one player per team: ignoring
        keepers and simply forcing the lowest 192 targets to average 96.5 pushed
        the 180 players who actually compete to an average of 100.5, when the
        picks available to them average 95.2. That mis-scaling raised the
        calibration error from 5.45 to 7.53 -- it made a marginal league worse,
        while every redraft league improved.
    """
    keeper_picks = keeper_picks or {}

    # The picks actually spent choosing somebody. A keeper's pick is consumed by
    # a player who was never on the board, so it is not a selection.
    selection_picks = [p for p in range(1, total_picks + 1) if p not in keeper_picks]
    if not selection_picks:
        return adp_target

    # Kept players never compete, so they must not shape the scale -- they are
    # mostly early-ADP, which would drag the average forward.
    candidates = adp_target if kept is None else adp_target[~np.asarray(kept)]

    # Fewer candidates than selections means the draft cannot fill itself, and
    # "who gets drafted" is not a meaningful set. The simulator raises on that
    # case; do not scale by a bogus factor first.
    if len(candidates) < len(selection_picks):
        return adp_target

    mean_target = candidates.nsmallest(len(selection_picks)).mean()
    if not mean_target or not np.isfinite(mean_target):
        return adp_target

    return adp_target * (float(np.mean(selection_picks)) / mean_target)


def fill_missing_stdev(df: pd.DataFrame, adp_column: str = "adp_target",
                       n_neighbors: int = 20) -> pd.Series:
    """Give every player a usable draft-position spread, with no trained model.

    "Width" here means how much a player's draft position varies from draft to
    draft — the standard deviation of his pick number. The simulator needs one
    for every player, but FFC cannot measure it for everybody, so this fills the
    gaps using progressively weaker evidence.

    Steps:
        1. Start from FFC's own spread, converting to numbers and copying so the
           input table is not modified.
        2. Estimate a spread from the observed high and low picks, dividing the
           range by 4, and use it wherever FFC gave nothing and the range is
           positive.
        3. Snapshot which rows are usable BEFORE filling any more in, so the
           result cannot depend on the order rows happen to be visited.
        4. For each row still missing a spread, gather the usable players at the
           same position, falling back to all usable players if that position has
           none at all.
        5. Measure how far each of those is from this player in ADP, take the
           closest `n_neighbors`, and use the median of their spreads.
        6. Apply MIN_STDEV as a hard floor, so nothing escapes with a zero.

    Args:
        df: The player table. Needs the columns `position`, `stdev`, `high`,
            `low`, and whatever `adp_column` names. `stdev` is NaN where FFC
            could not measure a spread, because the adapter turns FFC's
            meaningless 0 into NaN.
        adp_column: Which ADP column defines "nearby" for step 5. Defaults to the
            platform-shifted target.
        n_neighbors: How many same-position neighbours to take the median over.

    Returns:
        pd.Series: A width for every row, lined up with the input table's rows,
            guaranteed greater than zero.

    Raises:
        KeyError: If the table is missing one of the required columns.

    Note:
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
                pool_multiplier: float = POOL_MULTIPLIER,
                fit_scale: bool = True, adjust_keepers: bool = True) -> pd.DataFrame:
    """Assemble the one flat table the entire model runs on.

    This is the boundary between messy vendor data and clean model input. Every
    judgment about how a player's centre and width were derived is made here,
    once, so the simulator downstream sees only tidy numbers.

    Steps:
        1. Check that every required FFC column is present, and fail immediately
           if not.
        2. Copy the input so nothing done here modifies the caller's table.
        3. Compute `adp_target`: if platform ADP was supplied, line it up by
           canonical id and pass both to `apply_platform_shift` above. Otherwise
           use FFC's ADP as is.
        4. Fail loudly on any player missing an ADP or a position, BEFORE the
           pool cap below can quietly discard them — see the inline comment for
           why the order matters.
        5. Restate `adp_target` as when each player goes in THIS league rather
           than in a redraft one, with `adjust_for_keepers` above, then squeeze
           it onto the pick numbers this draft can actually hand out, with
           `fit_to_pick_space` above.
        6. Compute `stdev_target` with `fill_missing_stdev` above, run before the
           pool cap so deep players still have a full neighbourhood to draw from.
        7. Drop players whose ADP is beyond the pool cap, since they can never be
           selected but cost just as much to simulate.
        8. Attach any enrichment columns by canonical id. These never filter
           anything; a player lacking one just gets NaN.
        9. Seed `mu` and `sd` from the targets, ready for calibration to
           overwrite.
       10. Sort by `adp_target` and renumber the rows. This is the ONE sort, and
           the resulting order is frozen for the life of the artifact.

    Args:
        config: The league settings. Its `total_picks` sets the pool cap.
        ffc: One row per player from `FfcService.with_canonical_id`, with the
            columns `ffc_player_id`, `name`, `position`, `team`, `adp`, `stdev`,
            `high`, `low`, `times_drafted`, `bye`, and a nullable
            `canonical_id`.
        platform_adp: Blended platform ADP labelled by canonical id, from
            `blend_adp` above. None keeps pure FFC.
        enrichments: Extra columns to attach by canonical id, for example
            `{"projection": ..., "upside": ..., "risk": ...}`. Missing players
            get NaN; nothing is dropped for lacking them.
        platform_weight: Passed straight to `apply_platform_shift`.
        pool_multiplier: Drop players whose ADP is beyond `total_picks` times
            this.
        fit_scale: When True, rescale the centre so a draft this size can
            reproduce it (`fit_to_pick_space` above). Set False only to inspect
            the raw shifted targets; a simulation built with it off cannot hit
            them.
        adjust_keepers: When True, restate the centre for the keepers in this
            league (`adjust_for_keepers` above). No effect on a redraft league.
            Set False only to compare against raw vendor ADP.

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

    Note:
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

    # --- make the centre reproducible by a draft this size ---
    # Must run AFTER the shift and BEFORE anything reads adp_target. Without it
    # the platform blend's deeper scale forces every player to be drafted early,
    # and no amount of calibration can undo that -- see fit_to_pick_space.
    # Keepers change BOTH halves of the pick arithmetic: their picks are not
    # selections, and they themselves never compete for one. `keeper_picks`
    # maps an overall pick number to the kept player's canonical id.
    keeper_picks = config.keeper_picks
    kept = (table["canonical_id"].isin(set(keeper_picks.values()))
            if keeper_picks and "canonical_id" in table.columns else None)

    # --- restate the centre as when a player goes in THIS league ---
    # Runs before the scale fit: this corrects each player individually for the
    # keepers around him, while the fit that follows corrects the one remaining
    # aggregate difference between FFC's and the platforms' scales.
    if adjust_keepers:
        table["adp_target"] = adjust_for_keepers(
            table["adp_target"], keeper_picks=keeper_picks, kept=kept)

    if fit_scale:
        table["adp_target"] = fit_to_pick_space(
            table["adp_target"], config.total_picks,
            keeper_picks=keeper_picks, kept=kept,
        )

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

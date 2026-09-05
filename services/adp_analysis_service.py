"""Builds the one wide table behind the ADP Analysis page.

Four different opinions about when a player goes, lined up per player so the
gaps between them can be read directly:

    PlatAdp      what drafters on the platforms actually did
    Platform     the ranked board the platform SHOWS a drafter
    SimAdp       what this league's simulation produced
    FFB          the Fantasy Footballers' overall board, i.e. your own opinion

Each gap answers a different draft-day question, which is why the differences
are columns in their own right rather than something to eyeball.

KEEPERS ARE THE SUBTLETY. Vendor ADP is measured in redraft drafts where every
player is available. In a keeper league the kept players never reach the board,
so everyone else genuinely goes earlier. Every "adjusted" column here restates
its number for the league actually being drafted, and the two kinds of
adjustment are deliberately different:

  * An ADP is measured in PICKS, so it uses `table.adjust_for_keepers`, which
    both moves players up past vacated keepers and back down past the picks
    keepers consume.
  * A RANK is ordinal and has no picks in it, so it is simply re-ranked over the
    players still available.
"""

import numpy as np
import pandas as pd

from draft_model.calibrate import draft_rate
from draft_model.table import adjust_for_keepers


def rank_available(values: pd.Series, kept) -> pd.Series:
    """Rank players against only the ones still available to be drafted.

    A rank is a statement about a field of competitors. In a keeper league the
    kept players are not in that field, so ranking against them would answer a
    question nobody is asking. This drops them and renumbers everyone else.

    Steps:
        1. Copy the values so the caller's column is never modified, and blank
           out every kept player.
        2. Rank what remains, smallest value getting rank 1.
        3. Use "min" ranking so two players tied on a value share a rank rather
           than being separated arbitrarily.

    Args:
        values: The numbers to rank, lower being better -- an ADP, a board
            position, anything ordered that way.
        kept: One flag per player, True where he is being kept. Pass None for a
            redraft league, which ranks everybody.

    Returns:
        pd.Series: A rank per player on the same labels, starting at 1. NaN for
            a kept player and for anyone whose value was already missing.
    """
    available = values.astype("float64").copy()
    if kept is not None:
        available[np.asarray(kept, dtype=bool)] = np.nan
    return available.rank(method="min")


class AdpAnalysisService:
    """Assembles the per-player comparison of market, board, simulation, and rankings.

    Reads only; nothing here writes a draft, a plan, or an artifact. Everything
    is derived from one already-loaded simulation board plus the current vendor
    data.
    """

    def __init__(self, draft_sim_service, adp_comparison_service,
                 udk_top200_adapter, identity_repo):
        """Store the four collaborators this needs.

        Steps:
            1. Save each on the instance. Nothing is loaded here; the work
               happens when `build` is called.

        Args:
            draft_sim_service: Supplies the blended platform ADP.
            adp_comparison_service: Supplies each platform's own ADP and Yahoo's
                published board rank.
            udk_top200_adapter: Supplies the Fantasy Footballers' overall board.
            identity_repo: Resolves the Fantasy Footballers' player names to
                canonical ids so they can be joined to everything else.
        """
        self._draft_sim_service = draft_sim_service
        self._adp_comparison_service = adp_comparison_service
        self._udk_top200 = udk_top200_adapter
        self._identity_repo = identity_repo

    def build(self, config, board) -> pd.DataFrame:
        """Build the full comparison table, one row per player in the sim pool.

        The only method the page needs.

        Steps:
            1. Start from the board's own player table, which is already in
               simulation order and carries the canonical ids everything else
               joins on.
            2. Work out which players are kept, and which picks their teams
               spend, both of which every adjustment below depends on.
            3. Attach the blended platform ADP, then its keeper-adjusted twin via
               `adjust_for_keepers` from draft_model/table.py.
            4. Attach the drafting platform's own board rank with
               `_platform_rank` below.
            5. Attach the simulation's own ADP and the target it was aiming at,
               both already on the board.
            6. Attach the Fantasy Footballers' overall rank with `_ffb_rank`
               below.
            7. Rank each of those with `rank_available` above, which excludes
               kept players from the field.
            8. Compute the five difference columns, each of which answers one
               question named in its comment.

        Args:
            config: The league's DraftConfig. Its `platform` chooses which
                board rank is shown and its `keepers` drive every adjustment.
            board: A loaded `DraftBoard`, supplying the player table, the
                simulated ADP, and which players are kept.

        Returns:
            pd.DataFrame: One row per player in the simulation pool, with
                `canonical_id`, `name`, `position`, the four ADP/rank families,
                and the five `diff_*` columns. Every numeric column may be NaN
                where a source does not cover that player -- the Fantasy
                Footballers' board in particular has no kickers or defenses.

        Note:
            `plat_adp` is the pure ADP blend, deliberately built with the Yahoo
            board weighting turned OFF. The board is shown in its own column, so
            letting it bleed into the ADP column too would show the same signal
            twice and make the gap between them look smaller than it is.

            `target_adp` is ALREADY keeper-adjusted and scale-fitted, because
            that is what the simulation was built to reproduce. It has no
            "unadjusted" twin, which is why none is offered.
        """
        table = board.table
        out = pd.DataFrame({
            "canonical_id": table["canonical_id"].to_numpy(),
            "name": table["name"].to_numpy(),
            "position": table["position"].to_numpy(),
        })

        kept = board.kept_mask()
        keeper_picks = config.keeper_picks

        # Carried so the view can drop these rows. A kept player cannot be
        # drafted by anybody, so every rank and simulated column below is
        # necessarily blank for him -- the row would be noise, not information.
        out["is_kept"] = np.asarray(kept, dtype=bool)

        # --- the market: what drafters actually did ---
        # rank_weight=0.0 keeps this a pure ADP number; see the note above. The
        # drafting weight is the league's own either way, so this differs from
        # the blend below ONLY by the board, which is the point of showing both.
        market = self._draft_sim_service.platform_blend(
            config.scoring_format, config.platform, rank_weight=0.0,
            drafting_weight=config.drafting_platform_weight)
        out["plat_adp"] = table["canonical_id"].map(market).to_numpy(dtype=float)
        out["plat_adp_adj"] = adjust_for_keepers(
            out["plat_adp"], keeper_picks=keeper_picks, kept=kept).to_numpy()

        # --- the board: what the platform SHOWS a drafter ---
        out["platform_rank"] = self._platform_rank(config, table)

        # --- the blend the simulation was actually built from ---
        # The same three platforms as `plat_adp`, but with Yahoo's board mixed
        # into Yahoo's share. This is the number `build_table` receives, so it is
        # the missing link between what the market did and what the model aimed
        # at: market -> blend -> target -> simulated.
        blend = self._draft_sim_service.platform_blend(
            config.scoring_format, config.platform,
            rank_weight=config.board_rank_weight,
            drafting_weight=config.drafting_platform_weight)
        out["blend_adp"] = table["canonical_id"].map(blend).to_numpy(dtype=float)
        out["blend_adp_adj"] = adjust_for_keepers(
            out["blend_adp"], keeper_picks=keeper_picks, kept=kept).to_numpy()

        # --- the simulation, and the target it was told to hit ---
        out["sim_adp"] = board.simulated_adp
        # How OFTEN he was drafted at all, which is the context `sim_adp` needs.
        # A mean pick is conditional on being taken, so a player drafted in 0.6%
        # of simulations still shows a confident-looking ADP -- see the note.
        out["draft_rate"] = draft_rate(board.artifact.picks)
        out["target_adp"] = table["adp_target"].to_numpy(dtype=float)

        # --- your own rankings ---
        out["ffb_rank"] = self._ffb_rank(table)

        # --- ranks, always over the players still available ---
        out["plat_adp_rank"] = rank_available(out["plat_adp"], None)
        out["plat_adp_rank_adj"] = rank_available(out["plat_adp_adj"], kept)
        out["blend_adp_rank_adj"] = rank_available(out["blend_adp_adj"], kept)
        out["platform_rank_adj"] = rank_available(out["platform_rank"], kept)
        out["sim_adp_rank"] = rank_available(out["sim_adp"], kept)
        out["target_adp_rank"] = rank_available(out["target_adp"], kept)
        out["ffb_rank_adj"] = rank_available(out["ffb_rank"], kept)

        # --- the six questions ---
        # Every one is FIRST minus SECOND, matching its display heading, and a
        # POSITIVE number always means "goes LATER than this source rates him",
        # which is where value hides.
        # Which players does the board rate far above where they actually go?
        out["diff_market_vs_board"] = out["plat_adp_rank_adj"] - out["platform_rank_adj"]
        # Which players might go earlier than the simulation expects?
        out["diff_sim_vs_board"] = out["sim_adp_rank"] - out["platform_rank_adj"]
        # Where did the simulation fail to reproduce what it was aiming at?
        out["diff_sim_vs_target"] = out["sim_adp"] - out["target_adp"]
        # Where can you get value against your own rankings, on market price?
        out["diff_market_vs_ffb"] = out["plat_adp_rank"] - out["ffb_rank"]
        # And the same question inside THIS draft, simulation against your board.
        out["diff_sim_vs_ffb"] = out["sim_adp_rank"] - out["ffb_rank_adj"]
        # Two RANKED OPINIONS against each other, with no drafting in between:
        # where the platform's board and your own board simply disagree. Unlike
        # the rows above, neither side is a record of anybody's behaviour, so
        # this isolates the disagreement from how drafters actually act on it.
        out["diff_board_vs_ffb"] = out["platform_rank_adj"] - out["ffb_rank_adj"]

        return out

    def _platform_rank(self, config, table):
        """Get the ranked board the league's own platform puts in front of a drafter.

        ESPN and Yahoo both publish real ones. Sleeper does not, and falls back
        to its ADP order -- a stand-in rather than a real list. Its projections
        cannot substitute, since raw projected points ignore positional scarcity
        and would rank eleven quarterbacks inside the top fifteen.

        Steps:
            1. Ask the comparison service for this platform's published board.
            2. If it has none, take that platform's ADP column and rank it
               instead, so first in ADP order becomes rank 1.
            3. Map whichever series resulted onto the board's rows by canonical
               id.

        Args:
            config: The league's DraftConfig; only `platform` and
                `scoring_format` are read.
            table: The board's player table, supplying `canonical_id`.

        Returns:
            np.ndarray: One board position per row, lower being better. NaN for
                a player the platform does not list.
        """
        comparison = self._adp_comparison_service.compare(
            config.scoring_format).set_index("canonical_id")

        source = self._adp_comparison_service.board_rank(config.platform)
        if source is None or source.empty:
            # No published board for this platform (Sleeper). Fall back to its
            # ADP order, which is a stand-in rather than a real list.
            column = f"{config.platform}_adp"
            source = (comparison[column].dropna().rank(method="min")
                      if column in comparison.columns else pd.Series(dtype="float64"))

        return table["canonical_id"].map(source).to_numpy(dtype=float)

    def _ffb_rank(self, table):
        """Get the Fantasy Footballers' overall board position for each player.

        Their ranking is published by NAME, so it has to be resolved to canonical
        ids before it can be joined to anything else in the app.

        Steps:
            1. Load the board with the adapter, and give up early if it is empty.
            2. Resolve the names to canonical ids with
               `resolve_many_with_fallback` on the identity repository, passing
               positions so two players sharing a name can be told apart.
            3. Drop the names that did not resolve, since without an id there is
               nothing to join on.
            4. Keep the best rank per player, which collapses any accidental
               duplicate rows rather than letting them multiply the join.
            5. Map the result onto the board's rows.

        Args:
            table: The board's player table, supplying `canonical_id`.

        Returns:
            np.ndarray: One rank per row, 1 being the best. NaN for anyone the
                Fantasy Footballers do not rank, which includes EVERY kicker and
                team defense -- their board is skill positions only.
        """
        board = self._udk_top200.load()
        if board.empty:
            return np.full(len(table), np.nan)

        canonical_id = self._identity_repo.resolve_many_with_fallback(
            "udk", board["name"], board["position"])

        resolved = pd.DataFrame({
            "canonical_id": canonical_id,
            "ffb_rank": board["ffb_rank"].to_numpy(),
        }).dropna(subset=["canonical_id"])

        best = resolved.groupby("canonical_id")["ffb_rank"].min()
        return table["canonical_id"].map(best).to_numpy(dtype=float)

    def unresolved_ffb(self):
        """List Fantasy Footballers players whose names could not be matched.

        A diagnostic. Every name here is silently missing an FFB Rank on the
        page, and the fix is normally a hand-written player_id_map row.

        Steps:
            1. Load the board, returning nothing if it is empty.
            2. Hand its names and positions to `unresolved_with_fallback` on the
               identity repository.

        Returns:
            list: The unmatched names, with duplicates removed. Empty when
                everything resolved.
        """
        board = self._udk_top200.load()
        if board.empty:
            return []
        return self._identity_repo.unresolved_with_fallback(
            "udk", board["name"], board["position"])

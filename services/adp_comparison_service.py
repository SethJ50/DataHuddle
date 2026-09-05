"""
Combines ADP from ESPN, Sleeper, and Yahoo into one comparison table.

Driven by RosterService's UDK-ranked player universe: every roster player
appears (with blank ADP for any platform that doesn't have them), and any
ESPN/Sleeper/Yahoo player who ISN'T in the roster is excluded entirely.
"""

import pandas as pd
from scoring import ScoringFormat

class AdpComparisonService:
    """Puts three platforms' ADP side by side, one row per in-scope player.

    Comparing platforms is how you spot a player one site ranks far higher than
    the others, which is where value tends to hide. The player list comes from
    RosterService, so this table always covers the same players as the rest of
    the app.
    """


    def __init__(self, espn_adapter, sleeper_adapter, yahoo_adapter,
    identity_repo, roster_service, espn_board_adapter=None):
        """Store the three platform adapters and the two supporting services.

        Steps:
            1. Save each collaborator on the instance. Nothing is loaded yet;
               the data sources are only read when `compare` is called.

        Args:
            espn_adapter: An `EspnAdpAdapter` supplying ESPN's ADP.
            sleeper_adapter: A `SleeperAdpAdapter` supplying Sleeper's ADP.
            yahoo_adapter: A `YahooAdpAdapter` supplying Yahoo's ADP.
            identity_repo: A `PlayerIdentityRepo` used to turn each platform's
                player names into canonical ids.
            roster_service: Supplies the in-scope player list this table is
                built around.
            espn_board_adapter: An `EspnBoardRankAdapter` supplying ESPN's
                scraped draft-board order. Optional; without it `board_rank`
                reports that ESPN has no board.
        """
        self._espn = espn_adapter
        self._sleeper = sleeper_adapter
        self._yahoo = yahoo_adapter
        self._identity_repo = identity_repo
        self._roster_service = roster_service
        # Optional: ESPN's board arrives as its own scraped collection rather
        # than inside the ADP export. None means "not wired up", which
        # `board_rank` degrades to "no board" rather than failing.
        self._espn_board = espn_board_adapter

    def compare(self, fmt: ScoringFormat):
        """Build the full ADP comparison table, one row per in-scope player.

        The only method the comparison page needs. The result is deliberately
        anchored to the app's roster rather than to any platform's player list.

        Steps:
            1. Get the in-scope players from the roster service, keeping just the
               identifying columns.
            2. Load each platform's ADP and run it through `_prepare` below,
               which resolves names to canonical ids and names the ADP column.
            3. Merge each platform onto the roster with a left join, so every
               roster player survives whether or not that platform has him.
            4. Return the columns in a fixed order.

        Args:
            fmt: HALF_PPR or FULL_PPR, passed through to the ESPN and Sleeper
                adapters. Yahoo ignores it, since it publishes one ADP.

        Returns:
            pd.DataFrame: One row per roster player, with columns
                `canonical_id`, `display_name`, `headshot_url`, `position`,
                `espn_adp`, `yahoo_adp`, and `sleeper_adp`. A platform that does
                not rank a player leaves his cell blank.

        Note:
            Uses a left join from the roster, so every UDK-ranked player
            shows even if 0 of the 3 platforms have them (blank ADP in
            those columns); a platform player who ISN'T UDK-ranked never
            enters the result at all.
        """

        base = self._roster_service.roster()[
            ["canonical_id", "display_name", "headshot_url", "position"]
        ]

        espn = self._prepare("espn", self._espn.load(fmt), "espn_adp")
        sleeper = self._prepare("sleeper", self._sleeper.load(fmt), "sleeper_adp")
        yahoo = self._prepare("yahoo", self._yahoo.load(fmt), "yahoo_adp")

        # Left join: keep every roster player, attach each platform's ADP
        # where it resolves, blank otherwise.
        result = base.merge(espn, on="canonical_id", how="left")
        result = result.merge(sleeper, on="canonical_id", how="left")
        result = result.merge(yahoo, on="canonical_id", how="left")

        return result[["canonical_id", "display_name", "headshot_url", "position",
                        "espn_adp", "yahoo_adp", "sleeper_adp"]]


    def unresolved(self, source: str):
        """List one platform's player names that could not be matched to a player.

        A diagnostic. Every name here is silently absent from the comparison
        table, and the fix is normally a hand-written player_id_map row.

        Steps:
            1. Look up the adapter for the named platform. An unknown name
               raises here rather than returning nothing.
            2. Load its rows. The scoring format is fixed at HALF_PPR because
               which format is used does not change the set of NAMES a platform
               publishes.
            3. Hand the names and positions to `unresolved_with_fallback` on the
               identity repository.

        Args:
            source: Which platform to check: "espn", "sleeper", or "yahoo".

        Returns:
            list: The unmatched names, with duplicates removed. Empty when
                everything resolved.

        Raises:
            KeyError: If `source` is not one of the three platform names.
        """

        adapter = {"espn": self._espn, "sleeper": self._sleeper, "yahoo": self._yahoo}[source]
        df = adapter.load(ScoringFormat.HALF_PPR)
        return self._identity_repo.unresolved_with_fallback(source, df["name"], df["position"])

    def board_rank(self, source: str):
        """Get one platform's published board order, keyed by canonical player id.

        A platform's BOARD is the ranked list it puts in front of a drafter,
        which is a different signal from ADP -- ADP is what drafters did, the
        board is part of why. The draft simulation blends it into that
        platform's share of the ADP blend.

        Steps:
            1. Look up how this source supplies its board. Yahoo publishes a
               rank column inside its ADP export; ESPN's is a separate scraped
               collection; Sleeper publishes none at all.
            2. Return an empty result for a source with no board, so callers can
               treat "no board" and "board not loaded" identically.
            3. Load the rows and drop players with no rank, so an unranked
               player contributes nothing rather than a fabricated position.
            4. Hand the rest to `_prepare` below, which resolves names to
               canonical ids and collapses duplicates.

        Args:
            source: Which platform's board to load: "espn", "yahoo", or
                "sleeper". An unknown name returns empty rather than raising.

        Returns:
            pd.Series: Board position per player, labelled by `canonical_id`,
                lower being better. Empty when this source has no board.

        Note:
            The values are RAW ranks and are NOT comparable between sources or
            to a pick number. Yahoo's are sparse, running to 2473 across 1,175
            players; ESPN's are a dense 1..1027. Convert with
            `draft_model.table.rank_to_pick_scale` before comparing either to
            anything measured in picks.

            Deliberately NOT folded into `compare` above. That table feeds the
            ADP comparison page, whose job is showing what each platform
            actually publishes; a board-adjusted number there would misrepresent
            them.
        """
        if source == "yahoo":
            df = self._yahoo.load(ScoringFormat.HALF_PPR)
            column = "yahoo_rank"
        elif source == "espn" and self._espn_board is not None:
            df = self._espn_board.load()
            column = "espn_rank"
        else:
            # Sleeper publishes no board, and its projections cannot stand in --
            # sorting them by projected points puts eleven quarterbacks in the
            # top fifteen, because raw points ignore positional scarcity.
            return pd.Series(dtype="float64")

        if column not in df.columns:
            return pd.Series(dtype="float64")

        ranked = df.dropna(subset=[column])
        prepared = self._prepare(source, ranked, column, value_column=column)
        return prepared.set_index("canonical_id")[column]

    def _prepare(self, source, df, adp_col, value_column="adp"):
        """Turn one platform's raw ADP rows into a clean two-column lookup.

        Called once per platform by `compare` above. It exists so all three
        platforms are reduced to the same shape before being merged, whatever
        their raw data looked like.

        Steps:
            1. Resolve the platform's player names to canonical ids with
               `resolve_many_with_fallback` on the identity repository, passing
               positions so two players sharing a name can be told apart.
            2. Build a two-column table of id and ADP.
            3. Drop rows where resolution failed. These vanish rather than
               appearing blank, since without an id there is nothing to merge on.
            4. Group by player and keep the lowest ADP, which collapses any
               accidental duplicate rows rather than letting the merge multiply
               them.

        Args:
            source: Which platform this data came from, used as the lookup key
                when resolving names.
            df: That platform's raw rows, needing `name`, `position`, and `adp`
                columns.
            adp_col: What to call the ADP column in the output, for example
                "espn_adp".
            value_column: Which column of `df` to actually read. Defaults to
                "adp"; `yahoo_board_rank` above passes "yahoo_rank" so it can
                reuse the same name-resolution and de-duplication.

        Returns:
            pd.DataFrame: Two columns, `canonical_id` and whatever `adp_col`
                named, with exactly one row per resolved player.
        """

        canonical_id = self._identity_repo.resolve_many_with_fallback(source, df["name"], df["position"])
        out = pd.DataFrame({"canonical_id": canonical_id, adp_col: df[value_column]})
        out = out.dropna(subset=["canonical_id"])

        # Safety net: if a source ever has two rows for the same resolved
        # player, keep the lower (better) ADP rather than erroring.
        return out.groupby("canonical_id", as_index=False)[adp_col].min()
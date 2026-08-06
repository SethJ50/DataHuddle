"""Defines the app's player universe.

Only players ranked in UDK's QB/RB/WR/TE rankings are "in scope" for this
app. Everything else that needs a player list — Player Profile's dropdown,
ADP Platform Comparison, own-projections — filters down to this roster
rather than showing every player nflreadpy has ever heard of.
"""

import pandas as pd


class RosterService:
    """Decides which players the app considers to exist, and describes them.

    Every feature that shows a player list asks this service first. Taking UDK's
    rankings as the boundary keeps the app focused on draftable players rather
    than the roughly 25,000 people nflreadpy knows about.
    """

    def __init__(self, udk_rankings_adapter, identity_repo, player_directory):
        """Store the three collaborators this service needs.

        Steps:
            1. Save the rankings adapter, the identity repository, and the player
               directory on the instance. Nothing is loaded yet.

        Args:
            udk_rankings_adapter: A `UdkRankingsAdapter` supplying the ranked
                players that define the app's scope.
            identity_repo: A `PlayerIdentityRepo` used to turn UDK's player
                names into canonical ids.
            player_directory: A `PlayerDirectory` used to look up display names
                and headshots once the ids are known.
        """
        self._udk_adapter = udk_rankings_adapter
        self._identity_repo = identity_repo
        self._player_directory = player_directory

    def roster(self) -> pd.DataFrame:
        """Build the full in-scope player list, with names and photos attached.

        The core of this service. The other three methods are all narrower views
        of what this returns.

        Steps:
            1. Load UDK's combined rankings from the adapter.
            2. Resolve every UDK name to a canonical id with
               `resolve_many_with_fallback`, passing positions so two players
               sharing a name can be told apart.
            3. Attach those ids and drop anyone who could not be resolved.
            4. Sort by rank and keep one row per player, so a player mistakenly
               listed in two position files keeps only his best rank.
            5. Look up each player's display name and headshot through the player
               directory.
            6. Return the columns in a fixed order.

        Returns:
            pd.DataFrame: One row per in-scope player, with columns
                `canonical_id`, `display_name`, `headshot_url`, `position`,
                `team`, `bye_week`, `rank`, `points`, `risk`, `upside`, `adp`,
                and `tier`. UDK players who cannot be resolved to a canonical id
                are excluded — see `unresolved` below to find them.

        Note:
            No caching here, so each call re-resolves every name. Callers that
            need the roster repeatedly within one render should hold onto the
            result rather than calling again.
        """
        udk = self._udk_adapter.load()

        canonical_id = self._identity_repo.resolve_many_with_fallback(
            "udk", udk["name"], udk["position"]
        )
        udk = udk.assign(canonical_id=canonical_id).dropna(subset=["canonical_id"])
        udk = udk.sort_values("rank").drop_duplicates(subset="canonical_id", keep="first")

        udk["display_name"] = udk["canonical_id"].apply(self._player_directory.get_display_name)
        udk["headshot_url"] = udk["canonical_id"].apply(self._player_directory.get_headshot)

        return udk[[
            "canonical_id", "display_name", "headshot_url", "position", "team",
            "bye_week", "rank", "points", "risk", "upside", "adp", "tier",
        ]]

    def canonical_ids(self) -> set:
        """Get just the set of in-scope player IDs, with no other detail.

        Used as a filter: other services narrow their own data down to these ids
        so every part of the app talks about the same players. A set is returned
        because the only operation needed is "is this player in scope?", which
        sets answer instantly however large they get.

        Steps:
            1. Call `roster` above and collect its `canonical_id` column into a
               set.

        Returns:
            set: The canonical ids of every in-scope player.
        """
        return set(self.roster()["canonical_id"])

    def player_names(self) -> dict:
        """Get in-scope players as an ID-to-name mapping, for a dropdown menu.

        The roster-scoped counterpart to `PlayerDirectory.search_names`, shaped
        to be handed straight to a select widget's `choices=` argument: it shows
        the names and gives back the id.

        Steps:
            1. Call `roster` above.
            2. Zip its id and display-name columns into a dictionary.

        Returns:
            dict: Maps each in-scope player's canonical id to his display name,
                in roster order, which is best rank first.
        """
        roster = self.roster()
        return dict(zip(roster["canonical_id"], roster["display_name"]))

    def unresolved(self) -> list:
        """List UDK player names that could not be matched to any known player.

        A diagnostic. Every name here is a ranked player silently missing from
        the app's roster, so this is what to check when someone you expect is
        not in a dropdown.

        Steps:
            1. Load UDK's combined rankings from the adapter.
            2. Hand the names and positions to `unresolved_with_fallback` on the
               identity repository, which reports whatever neither the curated
               mapping nor the exact name match could resolve.

        Returns:
            list: The unmatched names, with duplicates removed. Each needs
                either a manual player_id_map row, or is a player nflreadpy has
                no record of yet, such as a very recent signing or draftee.
        """
        udk = self._udk_adapter.load()
        return self._identity_repo.unresolved_with_fallback("udk", udk["name"], udk["position"])
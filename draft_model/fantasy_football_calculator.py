"""Early exploratory script for hitting the Fantasy Football Calculator API.

Superseded by adapters/ffc_adapter.py, which is the version the app actually
uses: it normalizes the response into the app's canonical column names, handles
FFC's two different ways of returning no data, and never prints. This file is
kept as a scratch reference for what a raw FFC response looks like.
"""

import requests

class FantasyFootballCalculatorService:
    """Fetches raw, unprocessed draft data from the Fantasy Football Calculator API.

    Exploratory only — nothing in the app depends on this class. Use `FfcAdapter`
    in adapters/ffc_adapter.py instead, which returns a clean DataFrame rather
    than the API's raw records.
    """

    def __init__(self):
        """Set up an empty holder for fetched data.

        Steps:
            1. Create an empty dictionary on the instance. Nothing currently
               writes to it; it is left over from the exploratory version.
        """
        self.data = {}

    def _get_draft_data(self, format="ppr", teams=12, year=2026):
        """Fetch the raw list of player ADP records from the FFC API.

        Sends one HTTP request and hands back the response's player list exactly
        as FFC wrote it, with no renaming or cleaning.

        Steps:
            1. Build the request URL by appending the scoring format to the API
               path.
            2. Send the GET request and parse the JSON response body.
            3. Print the response's top-level keys and its first player record,
               which is what makes this an exploration script rather than
               library code.
            4. Return just the "players" portion of the response.

        Args:
            format: Scoring format, one of "ppr", "half-ppr", or "standard".
                This one is used.
            teams: Number of teams in the league. See the note below — this
                argument is currently ignored.
            year: Draft year. Also currently ignored.

        Returns:
            list: One dictionary per player, using FFC's own field names:
                `player_id`, `name`, `position`, `team`, `adp`, `adp_formatted`,
                `times_drafted`, `high`, `low`, `stdev`, and `bye`. Positions
                come back as FFC spells them, so kickers are "PK" and defenses
                are "DEF".

        Raises:
            requests.exceptions.RequestException: On a network failure.
            KeyError: If the response has no "players" key, which happens when
                FFC rejects the request.

        Note:
            Do not call frequently — FFC recomputes its data only once per day.

            The `teams` and `year` arguments are accepted but not passed to the
            request, which hardcodes teams=12 and year=2026. That is one of the
            reasons this file was superseded rather than fixed; use `FfcAdapter`
            if you need those to work.
        """

        r = requests.get("https://fantasyfootballcalculator.com/api/v1/adp/" + format,
                        params={"teams": 12, "year": 2026}).json()
        print(r.keys())
        print(r["players"][0])

        return r["players"]


if __name__ == "__main__":
    ffc = FantasyFootballCalculatorService()

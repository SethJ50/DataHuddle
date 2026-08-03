import requests

class FantasyFootballCalculatorService:
    def __init__(self):
        self.data = {}

    def _get_draft_data(self, format="ppr", teams=12, year=2026):
        """
            Purpose: Uses requests to fetch fantasy draft data from Fantasy Football Calculator API.

            Params:
                format: Scoring format, in {"ppr", "half-ppr", "standard"}
                teams: Number of teams in league
                year: Draft year

            Returns: A list of dicts, of the form
            {
                'player_id': ,
                'name': ,
                'position': ,
                'team': ,
                'adp': ,
                'adp_formatted': ,
                'times_drafted': ,
                'high': ,
                'low': ,
                'stdev': ,
                'bye': 
            }

            Note: Do not call frequently - data updates once per day.
        """

        r = requests.get("https://fantasyfootballcalculator.com/api/v1/adp/" + format,
                        params={"teams": 12, "year": 2026}).json()
        print(r.keys())
        print(r["players"][0])
        
        return r["players"]


if __name__ == "__main__":
    ffc = FantasyFootballCalculatorService()
"""Vendor-schema -> canonical-schema adapter for the Fantasy Football Calculator ADP API.

FFC is the app's only source of draft-position SPREAD (`stdev`), which the draft
model needs as much as it needs ADP itself: ADP says WHEN a player goes, stdev
says how much that varies from draft to draft. This module is the single place
that knows FFC's quirks -- everything downstream works from the canonical
DataFrame produced here and never touches a raw FFC field.

Verified against the live endpoint on 2026-07-30 (see draft_model/DESIGN.md 5.2).
Three behaviours are NOT what the API's shape implies:

  1. `teams` is echoed back but IGNORED -- ADP is byte-identical for 8/10/12/14.
     We still send a valid value (13 and 16 return HTTP 400), but it selects nothing.
  2. There are TWO ways to get no data: HTTP 400 for a year outside the accepted
     range, and HTTP 200 with an empty player list for an accepted year FFC has
     nothing for. 2025 is the second kind, sitting as a hole between populated
     years -- so "loop until the request fails" silently loses 2010-2024.
  3. Positions come back as "PK" and "DEF", not "K" and "DST".

Attribution: FFC asks that users of the free ADP API credit them with a link or
mention -- https://fantasyfootballcalculator.com
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import requests

from registry import canonical_position
from scoring import ScoringFormat

BASE_URL = "https://fantasyfootballcalculator.com/api/v1/adp"

# FFC rejects anything outside this set with HTTP 400. Note the values do NOT
# select different data (finding #1 above) -- we send one purely to be accepted.
VALID_TEAM_COUNTS = (8, 10, 12, 14)

# Our scoring vocabulary -> FFC's. FFC also exposes "2qb", plus "dynasty" and
# "rookie" -- but those two return zero players, so they aren't offered here.
FFC_FORMAT_BY_SCORING = {
    ScoringFormat.REGULAR: "standard",
    ScoringFormat.HALF_PPR: "half-ppr",
    ScoringFormat.FULL_PPR: "ppr",
}

USER_AGENT = "DataHuddle/1.0 (personal fantasy football tooling)"


@dataclass(frozen=True)
class FfcPull:
    """One attempt at pulling an FFC snapshot, successful or not.

    Exists so the caller has ONE thing to check. FFC can fail to give you data in
    two different ways, and a loop over many years must treat them identically or
    it will quietly skip the wrong things. Being a frozen dataclass means the
    fields below are filled in once, when the object is created, and cannot be
    changed afterwards.

    Attributes:
        ok: True only when at least one player row came back. Check this before
            touching `players`.
        players: Canonical DataFrame (see normalize_players). Empty when ok=False.
        meta: FFC's own description of the pull -- type, teams, rounds,
            total_drafts, start_date, end_date. The dates are the real
            observation window and belong on every stored snapshot.
        error: Human-readable reason when ok=False; None otherwise.
        requested: What we actually asked for, so a stored row can record its own
            provenance without the caller having to remember.
    """

    ok: bool
    players: pd.DataFrame = field(default_factory=pd.DataFrame)
    meta: dict = field(default_factory=dict)
    error: str | None = None
    requested: dict = field(default_factory=dict)


def snap_team_count(teams: int) -> int:
    """Round a league size to the nearest one FFC will accept.

    FFC only accepts league sizes of 8, 10, 12, or 14 and returns an error for
    anything else, so a 13-team league has to be sent as something else. This
    exists purely to avoid that error.

    Steps:
        1. Compare the requested team count against each value in
           VALID_TEAM_COUNTS and measure the distance to each.
        2. Return whichever accepted value is closest.

    Args:
        teams: Your league's real team count, for example 13.

    Returns:
        int: The nearest value in VALID_TEAM_COUNTS.

    Note:
        This is cosmetic. FFC returns identical ADP for every accepted team
        count, so this only prevents an HTTP 400 -- it does not tailor the data
        to your league. Do not let this function's existence imply otherwise.
    """
    return min(VALID_TEAM_COUNTS, key=lambda valid: abs(valid - teams))


def normalize_players(raw_players: list[dict]) -> pd.DataFrame:
    """Turn FFC's raw player records into the canonical table the app uses.

    Every FFC-specific quirk is handled here so that nothing downstream ever has
    to look at a raw FFC field: positions get renamed, unmeasurable spreads get
    marked as missing, and duplicate players get collapsed.

    Steps:
        1. Load the list of records into a DataFrame, one row per player.
        2. Build a new table column by column, converting each numeric field
           with `errors="coerce"` so a malformed value becomes NaN instead of
           raising, and mapping FFC's position names through
           `canonical_position` from registry.py so "PK" and "DEF" become "K"
           and "DST".
        3. Replace any `stdev` of exactly 0 with NaN -- see the note below.
        4. Sort by sample size and drop duplicate player ids, keeping the
           better-sampled row.
        5. Sort by ADP so the earliest-drafted player comes first, and renumber
           the rows.

    Args:
        raw_players: The list straight from the response's "players" key. Each
            record is a dictionary with the keys player_id, name, position,
            team, adp, adp_formatted, times_drafted, high, low, stdev, and bye.

    Returns:
        pd.DataFrame: One row per player, sorted by adp, with columns:
            ffc_player_id  int    FFC's own id; the join key for the sim table
            name           str    "Jahmyr Gibbs"
            position       str    canonical QB/RB/WR/TE/K/DST (PK and DEF remapped)
            team           str    NFL team abbreviation, e.g. "DET"
            adp            float  average overall pick number, 1-indexed
            stdev          float  spread of pick number; NaN when unmeasurable
            high           float  earliest pick he was taken at
            low            float  latest pick he was taken at
            times_drafted  float  sample size behind adp/stdev
            bye            float  bye week

    Raises:
        KeyError: If FFC's records are missing one of the expected keys, which
            would mean the API's shape changed.

    Note:
        `adp_formatted` ("1.02") is dropped deliberately: it bakes in FFC's
        assumed 12-team draft. ui_helpers.adp_to_round_pick() computes the same
        thing against YOUR league size.

        A stdev of exactly 0 is converted to NaN. Zero is not a measurement of
        "no spread" -- it means FFC had nothing to measure, e.g. a player taken
        5 times at the identical pick. Left as 0 he would be perfectly
        deterministic in the simulator; as NaN, the table builder is forced to
        fall back explicitly.

        The count columns stay float rather than int so a malformed row becomes a
        visible NaN instead of a silent 0.
    """
    # One row per player, columns exactly as FFC named them.
    df = pd.DataFrame(raw_players)

    out = pd.DataFrame({
        "ffc_player_id": pd.to_numeric(df["player_id"], errors="coerce"),
        "name":          df["name"].astype(str).str.strip(),
        "position":      df["position"].astype(str).map(canonical_position),
        "team":          df["team"].astype(str).str.strip(),
        "adp":           pd.to_numeric(df["adp"], errors="coerce"),
        "stdev":         pd.to_numeric(df["stdev"], errors="coerce"),
        "high":          pd.to_numeric(df["high"], errors="coerce"),
        "low":           pd.to_numeric(df["low"], errors="coerce"),
        "times_drafted": pd.to_numeric(df["times_drafted"], errors="coerce"),
        "bye":           pd.to_numeric(df["bye"], errors="coerce"),
    })

    # "Unmeasurable", not "zero" -- see Notes.
    out.loc[out["stdev"] == 0, "stdev"] = np.nan

    # Safety net: FFC ids should already be unique. If a duplicate ever appeared
    # and both rows survived, the simulator would treat one player as two people
    # and let him consume two picks. Keep the better-sampled row.
    out = (
        out.sort_values("times_drafted", ascending=False)
           .drop_duplicates("ffc_player_id", keep="first")
    )

    return out.sort_values("adp").reset_index(drop=True)


class FfcAdapter:
    """Fetches and normalizes Fantasy Football Calculator ADP snapshots.

    Unlike the other adapters in this package, this one talks to an HTTP API
    rather than a Mongo collection -- FFC is an external service, and caching its
    responses into Mongo is the job of the ingest scripts, not of this class.
    Keeping the network call here means the retry/timeout/user-agent policy lives
    in exactly one place.
    """

    def __init__(self, base_url: str = BASE_URL, timeout: float = 15.0, session=None):
        """Set up how this adapter will talk to the FFC service.

        All three settings have working defaults, so `FfcAdapter()` with no
        arguments is the normal way to build one. Tests override them.

        Steps:
            1. Store the base URL, the timeout, and the HTTP session on the
               instance.
            2. If no session was supplied, create a fresh `requests.Session`.

        Args:
            base_url: Where the ADP endpoint lives. Overridable so tests can
                point at a local stub instead of the real service.
            timeout: How many seconds to wait for a response before giving up.
            session: A `requests.Session` to reuse across calls, so the history
                backfill's dozens of requests share one network connection. A
                fresh one is created if omitted.
        """
        self._base_url = base_url
        self._timeout = timeout
        self._session = session or requests.Session()

    def fetch(self, fmt: ScoringFormat, year: int, teams: int = 12) -> FfcPull:
        """Pull one ADP snapshot, plus the metadata describing which drafts made it.

        This is the only method callers need. It always hands back an `FfcPull`
        rather than raising when a season has no data, which is what lets an
        ingest script loop over many years without special-casing the gaps.

        Steps:
            1. Translate the app's scoring format into FFC's own spelling using
               FFC_FORMAT_BY_SCORING.
            2. Round the team count to something FFC accepts with
               `snap_team_count` above, and record what was asked for.
            3. Send the GET request with the app's user-agent and timeout.
            4. If the status code is not 200, build a failed pull, using
               `_error_message` below to extract a readable reason.
            5. Read the JSON body and pull out its "players" list.
            6. If that list is empty, this is the second kind of no-data answer
               (see the module docstring), so return a failed pull too -- the
               caller then handles both kinds identically.
            7. Otherwise hand the raw records to `normalize_players` above and
               return a successful pull.

        Args:
            fmt: REGULAR, HALF_PPR, or FULL_PPR, translated to FFC's spelling.
            year: Which season to pull. Required -- omitting it makes FFC return
                a different season with no warning.
            teams: League size, rounded to something FFC accepts. Recorded for
                provenance only; it does not change the data that comes back.

        Returns:
            FfcPull: Always check `.ok` before using `.players`. When `.ok` is
                False, `.error` explains why and `.players` is empty.

        Raises:
            requests.exceptions.RequestException: For genuine transport failures
                such as a timeout or DNS error. Those mean something is wrong
                with YOU, not with the year, so they are allowed through rather
                than folded into an FfcPull.

        Note:
            "There is no 2025 data" is a normal answer when looping over years,
            not an exception, which is why it comes back as `ok=False`.

            FFC recomputes once daily. Call this from ingest scripts and cache
            the result; never call it per page request.
        """
        ffc_format = FFC_FORMAT_BY_SCORING[fmt]
        snapped = snap_team_count(teams)
        requested = {"format": ffc_format, "year": year, "teams": snapped}

        response = self._session.get(
            f"{self._base_url}/{ffc_format}",
            params={"teams": snapped, "year": year},
            timeout=self._timeout,
            headers={"User-Agent": USER_AGENT},
        )

        # Signal 1: we sent something FFC won't accept. The body is still JSON and
        # carries a readable reason ("Invalid year"), so pass that along rather
        # than a bare status code.
        if response.status_code != 200:
            return FfcPull(ok=False, error=_error_message(response), requested=requested)

        payload = response.json()
        raw_players = payload.get("players") or []

        # Signal 2: a perfectly valid request for a season FFC simply has no
        # drafts for. Returned as ok=False so a caller looping over years handles
        # it the same way as signal 1 -- the whole reason FfcPull exists.
        if not raw_players:
            return FfcPull(
                ok=False,
                meta=payload.get("meta") or {},
                error=f"no players returned for {year} {ffc_format}",
                requested=requested,
            )

        return FfcPull(
            ok=True,
            players=normalize_players(raw_players),
            meta=payload.get("meta") or {},
            requested=requested,
        )


def _error_message(response) -> str:
    """Build the most readable failure reason available from a bad FFC response.

    FFC normally explains its own errors in the response body, which is far more
    useful than a bare status code. But that cannot be relied on for every
    failure, so this degrades gracefully rather than raising while trying to
    describe another error.

    Steps:
        1. Try to read the response body as JSON.
        2. Look for an "errors" list, either at the top level or nested under
           "meta".
        3. If one is there, join its entries into a single line alongside the
           status code.
        4. If the body was not JSON at all, which happens when a proxy or outage
           returns an HTML error page, fall through and return just the status
           code.

    Args:
        response: The `requests` response object whose status code was not 200.

    Returns:
        str: Something like "HTTP 400: Invalid year" when FFC explained itself,
            or plain "HTTP 502" when it did not.
    """
    try:
        payload = response.json()
        errors = payload.get("errors") or payload.get("meta", {}).get("errors")
        if errors:
            return f"HTTP {response.status_code}: {'; '.join(errors)}"
    except ValueError:
        pass
    return f"HTTP {response.status_code}"

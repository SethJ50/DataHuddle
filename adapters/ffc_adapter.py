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
    it will quietly skip the wrong things.

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
    """
    Purpose: Coerce a league size to one FFC will accept.

    Parameters:
        teams (int): Your league's real team count, e.g. 13.

    Returns:
        int: The nearest value in VALID_TEAM_COUNTS.

    Notes:
        Cosmetic. FFC returns identical ADP for every accepted team count, so
        this only prevents an HTTP 400 -- it does not tailor the data to your
        league. Do not let this function's existence imply otherwise.
    """
    return min(VALID_TEAM_COUNTS, key=lambda valid: abs(valid - teams))


def normalize_players(raw_players: list[dict]) -> pd.DataFrame:
    """
    Purpose: Turn FFC's raw player dicts into the canonical table the app uses.

    Parameters:
        raw_players (list[dict]): Straight from the response's "players" key. Each
            dict has: player_id, name, position, team, adp, adp_formatted,
            times_drafted, high, low, stdev, bye.

    Returns:
        pd.DataFrame -- one row per player, sorted by adp, with columns:
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

    Notes:
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
        """
        Parameters:
            base_url (str): Overridable so tests can point at a local stub.
            timeout (float): Seconds before a request is abandoned.
            session (requests.Session | None): Reused across calls so the history
                backfill (dozens of requests) shares one TCP connection. A fresh
                Session is created if omitted.
        """
        self._base_url = base_url
        self._timeout = timeout
        self._session = session or requests.Session()

    def fetch(self, fmt: ScoringFormat, year: int, teams: int = 12) -> FfcPull:
        """
        Purpose: Pull one ADP snapshot and hand back a normalized table plus the
            metadata describing which drafts produced it.

        Parameters:
            fmt (ScoringFormat): REGULAR / HALF_PPR / FULL_PPR, translated to
                FFC's own spelling.
            year (int): Season to pull. Required -- omitting it returns a
                different season with no warning.
            teams (int): League size, snapped to something FFC accepts. Recorded
                for provenance only; it does not change the data.

        Returns:
            FfcPull. Always check `.ok` before using `.players`.

        Notes:
            Never raises for an absent season -- "there is no 2025 data" is a
            normal answer when you are looping over years, not an exception.
            Genuine transport failures (timeout, DNS) still propagate, because
            those mean something is wrong with YOU, not with the year.

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
    """Best-effort human-readable reason from a non-200 FFC response.

    FFC returns JSON even on errors, shaped {status, errors: [...]}, but we
    cannot rely on that for every failure (a proxy or outage may return HTML),
    so fall back to the status code.
    """
    try:
        payload = response.json()
        errors = payload.get("errors") or payload.get("meta", {}).get("errors")
        if errors:
            return f"HTTP {response.status_code}: {'; '.join(errors)}"
    except ValueError:
        pass
    return f"HTTP {response.status_code}"

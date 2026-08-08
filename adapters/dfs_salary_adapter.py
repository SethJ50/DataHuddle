"""Reads the salary exports the DFS sites publish, and makes them agree.

FanDuel and DraftKings both hand out a CSV of who is on a slate and what they
cost. The two files describe the same thing and share almost nothing: different
column names, different id schemes, different ways of writing a player's name and
a team's defence. Neither carries the id the rest of this app uses.

This turns either one into the same handful of columns, with player ids attached
where they can be worked out.
"""

import re
import unicodedata

import pandas as pd

SITE_FANDUEL = "FanDuel"
SITE_DRAFTKINGS = "DraftKings"

COLUMNS = ["site", "season", "week", "site_player_id", "name", "canonical_id",
           "position", "roster_positions", "salary", "team", "opponent",
           "game", "site_projection", "injury_status"]
"""What every salary row looks like once the site differences are gone."""

TEAM_ALIASES = {
    "JAC": "JAX",     # FanDuel writes JAC; the NFL's own data says JAX
    "LAR": "LA",      # some exports spell the Rams out; nflverse uses LA
    "WSH": "WAS",
    "SD": "LAC",      # relocations, in case an old file turns up
    "OAK": "LV",
    "STL": "LA",
    "LVR": "LV",
}
"""Team abbreviations the sites spell differently from the NFL's own data.

Only `JAC` actually appears in the current exports, and only from FanDuel -- but
a single unmapped abbreviation silently costs that team every statistic on the
sheet, showing a real player with a real price and blank everything else. The
rest are here because they cost nothing and a site changing its mind is cheaper
to have already handled.
"""

NAME_SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")
"""Generational suffixes to drop when comparing names.

The sites write "James Cook III" where the NFL's own data says "James Cook", and
that single difference accounts for most of the names that fail to match.
"""


def normalise_name(name):
    """Reduce a player's name to something two sources can be compared on.

    Steps:
        1. Strip accents, so "Amon-Ra St. Brown" written either way lands in the
           same place.
        2. Lower the case and remove full stops, apostrophes and hyphens.
        3. Drop a generational suffix -- see `NAME_SUFFIXES` above.
        4. Collapse the remaining spaces.

    Args:
        name: The name as the site wrote it.

    Returns:
        str: The comparable form, such as `"james cook"`.

    Note:
        Matching on exact names resolves about 55% of a slate. This takes it to
        roughly 96%. The rest are nicknames the sites use and the NFL does not --
        "Hollywood Brown" for Marquise Brown -- which no amount of tidying will
        fix and which need a manual mapping instead.
    """
    plain = unicodedata.normalize("NFKD", str(name))
    plain = plain.encode("ascii", "ignore").decode()
    plain = plain.lower().replace(".", "").replace("'", "").replace("-", " ")
    plain = re.sub(rf"\b({'|'.join(NAME_SUFFIXES)})\b", "", plain)
    return " ".join(plain.split())


def read_fanduel(path, season=None, week=None, name_lookup=None):
    """Read a FanDuel salary export into the shared shape.

    Steps:
        1. Read the file.
        2. Rename its columns to the shared ones, taking the player's name from
           `Nickname`, which is the full name -- `First Name` and `Last Name`
           are stored separately and do not always concatenate correctly.
        3. Rewrite the position `D` as `DST`, which is what everything else in
           this app calls a team defence.
        4. Attach player ids with `_resolve` below.

    Args:
        path: The CSV to read.
        season: Which season this slate belongs to.
        week: Which week.
        name_lookup: Normalised name to `canonical_id`. None leaves every id
            blank, which is fine for reading the file but not for using it.

    Returns:
        pd.DataFrame: The columns listed in `COLUMNS` above.
    """
    raw = pd.read_csv(path)

    frame = pd.DataFrame({
        "site": SITE_FANDUEL,
        "site_player_id": raw["Id"].astype(str),
        "name": raw["Nickname"].astype(str),
        "position": raw["Position"].replace({"D": "DST"}),
        "roster_positions": raw.get("Roster Position", pd.Series(dtype=str)),
        "salary": pd.to_numeric(raw["Salary"], errors="coerce"),
        "team": raw["Team"].astype(str),
        "opponent": raw["Opponent"].astype(str),
        "game": raw["Game"].astype(str),
        "site_projection": pd.to_numeric(raw.get("FPPG"), errors="coerce"),
        "injury_status": raw.get("Injury Indicator", pd.Series(dtype=str)),
    })
    return _finish(frame, season, week, name_lookup)


def read_draftkings(path, season=None, week=None, name_lookup=None):
    """Read a DraftKings salary export into the shared shape.

    Steps:
        1. Read the file, which starts with a byte-order mark that would
           otherwise become part of the first column's name.
        2. Rename its columns to the shared ones.
        3. Split the opponent out of the game string, since DraftKings gives the
           fixture but not who each player is facing.
        4. Attach player ids with `_resolve` below.

    Args:
        path: The CSV to read.
        season: Which season this slate belongs to.
        week: Which week.
        name_lookup: Normalised name to `canonical_id`.

    Returns:
        pd.DataFrame: The columns listed in `COLUMNS` above.
    """
    raw = pd.read_csv(path, encoding="utf-8-sig")

    # "NO@DET 09/13/2026 01:00PM ET" -> "NO@DET"
    fixture = raw["Game Info"].astype(str).str.split(" ").str[0]

    frame = pd.DataFrame({
        "site": SITE_DRAFTKINGS,
        "site_player_id": raw["ID"].astype(str),
        "name": raw["Name"].astype(str),
        "position": raw["Position"],
        "roster_positions": raw.get("Roster Position", pd.Series(dtype=str)),
        "salary": pd.to_numeric(raw["Salary"], errors="coerce"),
        "team": raw["TeamAbbrev"].astype(str),
        "game": fixture,
        "site_projection": pd.to_numeric(raw.get("AvgPointsPerGame"),
                                         errors="coerce"),
        "injury_status": raw.get("Status", pd.Series(dtype=str)),
    })
    frame["opponent"] = _opponent_from(fixture, frame["team"])
    return _finish(frame, season, week, name_lookup)


def _opponent_from(fixture, team):
    """Work out who a player is facing, from the fixture and his own team.

    Steps:
        1. Split the fixture on its "@" into the away and home sides.
        2. Give back whichever of the two is not the player's own team.

    Args:
        fixture: Strings like `"NO@DET"`.
        team: Each player's own team abbreviation.

    Returns:
        pd.Series: The opposing team's abbreviation, blank where the fixture
            could not be read.
    """
    sides = fixture.str.split("@", expand=True)
    if sides.shape[1] < 2:
        return pd.Series([""] * len(fixture), index=fixture.index)

    away, home = sides[0].str.strip(), sides[1].str.strip()
    return away.where(home == team, home)


def _finish(frame, season, week, name_lookup):
    """Tag a slate with its week and attach player ids.

    Steps:
        1. Record which season and week the slate is for.
        2. Look each player up by normalised name, leaving the id blank where
           nothing matched.
        3. Put the columns in the shared order.

    Args:
        frame: The part-built slate.
        season: Which season.
        week: Which week.
        name_lookup: Normalised name to `canonical_id`, or None.

    Returns:
        pd.DataFrame: The columns listed in `COLUMNS` above.

    Note:
        TEAM ABBREVIATIONS ARE NORMALISED HERE, at the point the file is read,
        rather than wherever they are later joined. A site's spelling is a fact
        about the file and nothing downstream should have to know about it.

        A BLANK ID IS A NORMAL OUTCOME, not a failure. Team defences have no
        player id at all in this app, and roughly one skill player in twenty-five
        goes by a name the NFL's own data does not use. Those rows keep their
        salary and simply have nothing to join statistics to, which the pages
        show as empty cells rather than hiding.
    """
    frame = frame.copy()
    frame["season"] = season
    frame["week"] = week

    # Bring the team names into line with the NFL's own before anything is
    # stored, so nothing downstream has to know a site spells one differently.
    for column in ("team", "opponent"):
        if column in frame.columns:
            frame[column] = frame[column].replace(TEAM_ALIASES)

    lookup = name_lookup or {}
    keys = frame["name"].map(normalise_name)
    frame["canonical_id"] = keys.map(lookup)

    # A defence is a team, not a person, so it never has one.
    frame.loc[frame["position"] == "DST", "canonical_id"] = None

    return frame.reindex(columns=COLUMNS)


def build_name_lookup(players):
    """Build the name-to-id lookup the readers above need.

    Steps:
        1. Keep everyone with both a name and an id.
        2. Put the skill positions first, so that where two players share a name
           the one a salary file is likely to mean wins -- see the note.
        3. Index each by his normalised name, keeping the first of any
           duplicates.

    Args:
        players: nflreadpy's player reference, needing `display_name`,
            `position` and `gsis_id`.

    Returns:
        dict: Normalised name to `canonical_id`.

    Note:
        EVERY POSITION IS INDEXED, not just the skill ones. The sites list
        fullbacks, tackles and long snappers under RB, TE and WR because they
        are eligible receivers, and Travis Hunter is a cornerback who also plays
        wide receiver. Restricting this to quarterbacks, backs, receivers and
        tight ends leaves about thirty rows a slate unmatched for no reason.

        Names are not unique -- there have been several Mike Williamses -- which
        is why the skill positions go in first. It is still a guess, and it is
        why the loader reports what it could not resolve rather than presenting
        the match rate as a job finished.
    """
    known = players.dropna(subset=["gsis_id", "display_name"]).copy()

    skill = known["position"].isin(["QB", "RB", "WR", "TE"])
    ordered = pd.concat([known[skill], known[~skill]])

    lookup = {}
    for name, player_id in zip(ordered["display_name"], ordered["gsis_id"]):
        lookup.setdefault(normalise_name(name), player_id)
    return lookup

"""Copies the notes and tags you have written from one league to another.

Markings and team notes are scoped to a draft, which is right -- a player can be
a target in one league and a fade in another. But most of what you write is an
opinion about the PLAYER, not about the league, and re-typing it into a second
league is work nobody should have to do.

Nothing here overwrites. Where both leagues have something to say about the same
player, the tags are combined and both notes are kept, labelled with where each
came from. That makes the result occasionally untidy rather than occasionally
destructive, which is the right way round when there is no undo.
"""

from typing import NamedTuple

from registry import MARKING_CATEGORIES

ADDED = "add"
MERGED = "merge"
UNCHANGED = "unchanged"
"""What happened to one player or team. See `Change` below."""

SEPARATOR = "\n\n— from {source} —\n"
"""How a copied note is joined onto one that was already there.

Naming the league it came from matters: months later, a note that reads like
your own opinion and a note you imported are indistinguishable without it.
"""

DEFAULT_SOURCE_LABEL = "another league"
"""Used when the caller does not say which league a note came from."""


class Change(NamedTuple):
    """One player or team the copy would touch.

    Attributes:
        kind: `"player"` or `"team"`.
        key: The canonical id or team abbreviation.
        label: What to show a person -- a player's name, or the team's
            abbreviation. Falls back to the key when no name is known.
        action: `ADDED`, `MERGED` or `UNCHANGED`.
        detail: A short plain-English description of the change, for the preview
            table.
    """

    kind: str
    key: str
    label: str
    action: str
    detail: str


class TransferReport(NamedTuple):
    """What a copy did, or would do.

    The same shape whether it was a preview or the real thing, so a page can
    show one before and after without special-casing either.

    Attributes:
        changes: Every `Change` considered, including the ones that came to
            nothing -- an unchanged row is information too.
        applied: True if this was written to the database, False for a preview.
    """

    changes: tuple
    applied: bool

    def of_kind(self, kind):
        """The changes for players, or for teams."""
        return tuple(c for c in self.changes if c.kind == kind)

    def counts(self, kind=None):
        """Count the changes by what happened to them.

        Args:
            kind: `"player"`, `"team"`, or None for everything.

        Returns:
            dict: `{ADDED: n, MERGED: n, UNCHANGED: n}`.
        """
        rows = self.changes if kind is None else self.of_kind(kind)
        return {action: sum(1 for c in rows if c.action == action)
                for action in (ADDED, MERGED, UNCHANGED)}

    @property
    def touched(self):
        """How many players and teams would actually change."""
        counted = self.counts()
        return counted[ADDED] + counted[MERGED]


def merge_categories(target, source):
    """Combine two lists of tags without repeating any.

    Steps:
        1. Put the known categories in the order the registry declares them, so
           two players tagged the same way list them the same way.
        2. Append anything neither list recognises, which is how a tag removed
           from the registry survives rather than being silently dropped.

    Args:
        target: The tags already in the destination league.
        source: The tags coming from the other league.

    Returns:
        list: The combined tags.
    """
    combined = set(target or []) | set(source or [])

    known = [name for name in MARKING_CATEGORIES if name in combined]
    unknown = sorted(combined - set(MARKING_CATEGORIES))
    return known + unknown


def merge_notes(target, source, source_label=DEFAULT_SOURCE_LABEL):
    """Join two notes together, keeping both.

    Steps:
        1. If either side is empty, the other one is the answer.
        2. If the incoming note already appears in the destination, leave it
           alone -- see the note.
        3. Otherwise append it under a heading naming where it came from.

    Args:
        target: The note already in the destination league.
        source: The note coming from the other league.
        source_label: The name of the league it came from.

    Returns:
        str: The combined note.

    Note:
        STEP 2 IS WHAT MAKES COPYING TWICE HARMLESS. Without it, running the same
        copy again would append the same paragraph a second time, and again a
        third -- which is exactly what somebody does after adding one tag in the
        source league and wanting it carried over.
    """
    target = (target or "").strip()
    source = (source or "").strip()

    if not source:
        return target
    if not target:
        return source
    if source in target:
        return target

    return target + SEPARATOR.format(source=source_label) + source


class NotesTransferService:
    """Copies markings and team notes between two saved leagues.

    Takes the two services it works through rather than reaching into the
    database itself, which is what lets the tests run without one.
    """

    def __init__(self, markings_service, team_notes_service):
        """Wire up the service.

        Args:
            markings_service: A `PlayerMarkingsService`.
            team_notes_service: A `TeamNotesService`.
        """
        self._markings = markings_service
        self._team_notes = team_notes_service

    def preview(self, source_draft_id, target_draft_id, source_label=None,
                names=None) -> TransferReport:
        """Work out what a copy would do, without doing it.

        Steps:
            1. Run the same comparison the real copy runs, with writing turned
               off -- see the note.

        Args:
            source_draft_id: The league to copy FROM.
            target_draft_id: The league to copy INTO.
            source_label: The source league's name, used in the note headings.
            names: Optional `{canonical_id: display_name}` so the preview can
                show who a player is rather than an id.

        Returns:
            TransferReport: With `applied` False.

        Note:
            The preview and the copy are THE SAME CODE with one flag different.
            Two implementations would eventually disagree, and a preview that
            disagrees with what happens is worse than no preview.
        """
        return self._transfer(source_draft_id, target_draft_id, source_label,
                              names, apply=False)

    def copy(self, source_draft_id, target_draft_id, source_label=None,
             names=None) -> TransferReport:
        """Copy every marking and team note from one league into another.

        Steps:
            1. Run the comparison with writing turned on.

        Args:
            source_draft_id: The league to copy FROM.
            target_draft_id: The league to copy INTO.
            source_label: The source league's name, used in the note headings.
            names: Optional `{canonical_id: display_name}` for the report.

        Returns:
            TransferReport: With `applied` True.

        Raises:
            ValueError: If the two leagues are the same one.
        """
        return self._transfer(source_draft_id, target_draft_id, source_label,
                              names, apply=True)

    def _transfer(self, source_draft_id, target_draft_id, source_label, names,
                  apply):
        """Compare the two leagues, and optionally write the result.

        Steps:
            1. Refuse to copy a league into itself, which would merge every note
               with a copy of itself.
            2. Read both leagues' markings, and index the destination by player.
               Source rows that say nothing are skipped -- see `_says_nothing`
               below.
            3. For each marked player in the source, work out the combined tags
               and note, and record whether that is a new entry, a genuine
               change, or no change at all.
            4. Write it, if this is not a preview.
            5. Do the same for team notes.

        Args:
            source_draft_id: The league to copy FROM.
            target_draft_id: The league to copy INTO.
            source_label: The source league's name.
            names: Optional player-name lookup.
            apply: False to work everything out and write nothing.

        Returns:
            TransferReport: Everything considered, in source order.

        Raises:
            ValueError: If the two ids are the same.
        """
        if source_draft_id == target_draft_id:
            raise ValueError("cannot copy a league into itself")

        label = source_label or DEFAULT_SOURCE_LABEL
        names = names or {}
        changes = []

        # ---- player markings ----
        existing = {row.get("canonical_id"): row
                    for row in self._markings.all_for_draft(target_draft_id)}

        for row in self._markings.all_for_draft(source_draft_id):
            player = row.get("canonical_id")
            if not player or _says_nothing(row):
                continue

            current = existing.get(player, {})
            categories = merge_categories(current.get("categories"),
                                          row.get("categories"))
            notes = merge_notes(current.get("notes"), row.get("notes"), label)

            action, detail = _classify(
                had_row=player in existing,
                same=(categories == list(current.get("categories") or [])
                      and notes == (current.get("notes") or "").strip()),
                before=current, categories=categories, notes=notes,
            )
            changes.append(Change("player", player,
                                  names.get(player, player), action, detail))

            if apply and action != UNCHANGED:
                self._markings.save(target_draft_id, player, categories, notes)

        # ---- team notes ----
        existing_teams = {row.get("team_abbr"): row.get("notes", "")
                          for row in self._team_notes.all_for_draft(target_draft_id)}

        for row in self._team_notes.all_for_draft(source_draft_id):
            team = row.get("team_abbr")
            if not team or not (row.get("notes") or "").strip():
                continue

            current = existing_teams.get(team, "")
            notes = merge_notes(current, row.get("notes"), label)

            if team not in existing_teams:
                action, detail = ADDED, "note copied across"
            elif notes == current.strip():
                action, detail = UNCHANGED, "already has this note"
            else:
                action, detail = MERGED, "both notes kept"

            changes.append(Change("team", team, team, action, detail))

            if apply and action != UNCHANGED:
                self._team_notes.save(target_draft_id, team, notes)

        return TransferReport(tuple(changes), applied=apply)


def _says_nothing(row):
    """Is this marking empty -- no tags and no note?

    Steps:
        1. Check whether the row has any categories or any note text.

    Args:
        row: One stored marking.

    Returns:
        bool: True if there is nothing in it worth copying.

    Note:
        THESE ARE COMMON, and skipping them matters. Opening the marking editor
        on a player and saving without typing leaves a row with an empty tag list
        and an empty note -- nine of the ten markings in one real draft were
        exactly that. Copying them would create the same empty rows in the
        destination and report "10 players added" when one player had anything
        to say.
    """
    return not (row.get("categories") or []) and not (row.get("notes") or "").strip()


def _classify(had_row, same, before, categories, notes):
    """Decide what happened to one player, and say it in a few words.

    Steps:
        1. A player the destination had never heard of is a straight addition.
        2. A player whose combined tags and note match what was already there is
           unchanged -- which is what a second run of the same copy produces.
        3. Anything else is a merge, described by what actually grew.

    Args:
        had_row: Whether the destination already had this player.
        same: Whether the combined result equals what was already stored.
        before: The destination's existing record.
        categories: The combined tags.
        notes: The combined note.

    Returns:
        tuple: `(action, detail)`.
    """
    if not had_row:
        gained = []
        if categories:
            gained.append(f"{len(categories)} tag{'s' if len(categories) > 1 else ''}")
        if notes:
            gained.append("a note")
        return ADDED, " and ".join(gained) + " copied across" if gained else "nothing to copy"

    if same:
        return UNCHANGED, "already has everything"

    was = set(before.get("categories") or [])
    new_tags = [tag for tag in categories if tag not in was]
    grew = bool(notes) and notes != (before.get("notes") or "").strip()

    parts = []
    if new_tags:
        parts.append(f"+{len(new_tags)} tag{'s' if len(new_tags) > 1 else ''}")
    if grew:
        parts.append("note appended")
    return MERGED, ", ".join(parts) or "updated"

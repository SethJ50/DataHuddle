"""Page for creating, editing, and deleting the leagues you draft in.

A "draft" here is a saved set of league settings — size, scoring, your slot,
your starting lineup, your keepers — not a record of picks made. Almost
everything else in the app is scoped to one of these, so this page is usually
the first stop when setting up.

Two tabs share the same three helper widgets defined at the top of the file, so
editing an existing league and creating a new one stay consistent.

Like every file in pages/, this is a script rather than a set of functions:
Streamlit runs it top to bottom each time the page is shown, or any widget on
it is changed.
"""

import streamlit as st

from streamlit_state import get_app_context
from scoring import ScoringFormat
from draft_model.config import DEFAULT_STARTING_SLOTS, normalize_keepers
from draft_model.mechanics import picks_for_slot

ctx = get_app_context()

# Choices shared by the edit and create forms.
PLATFORMS = ["espn", "yahoo", "sleeper"]
FORMATS = list(ScoringFormat)  # REGULAR, HALF_PPR, FULL_PPR

# Order the lineup slots are shown in. FLEX sits after the positions it draws
# from, since that's how it reads on a real league settings page.
LINEUP_SLOTS = ["QB", "RB", "WR", "TE", "FLEX", "K", "DST"]


@st.cache_data(ttl="1h", show_spinner=False)
def player_name_options():
    """Load the player list for the keeper picker, reusing it between reruns.

    Steps:
        1. Ask the roster service for its id-to-name mapping of every in-scope
           player.

    Returns:
        dict: Maps each player's canonical id to his display name.

    Note:
        Cached because RosterService.player_names() costs ~0.35s per call, and
        Streamlit reruns this whole script on ANY widget interaction. Without the
        cache, every keystroke in the draft name field and every click on a
        lineup stepper would pay that cost, which is very noticeable.

        ctx is deliberately not a parameter -- it's an unhashable shared resource,
        and closing over it keeps the cache key on nothing at all, which is right
        since the roster only changes when load_data runs.
    """
    return ctx.roster_service.player_names()


def lineup_editor(key_prefix, current):
    """Draw one number box per starting lineup slot, and collect what was entered.

    Shared by both tabs so the edit and create forms cannot drift apart. In
    Streamlit a widget returns its current value as soon as it is drawn, which is
    why this both renders the inputs and returns the answers.

    Steps:
        1. Start an empty dictionary to collect the counts.
        2. Lay the inputs out in one horizontal row.
        3. For each slot in LINEUP_SLOTS, draw a number box seeded from the
           current value, treating an absent slot as 0.
        4. Give each widget a key built from the prefix, so the two tabs get
           separate widgets rather than sharing state.

    Args:
        key_prefix: A prefix unique to this form, which is what keeps the edit
            and create tabs from sharing widget state.
        current: The existing slot counts used to seed the inputs.

    Returns:
        dict: Maps each position to the starter count now entered.

    Note:
        This is what sets the VORP replacement level. A 12-team league starting
        2 RBs has its replacement RB at roughly RB30 once the flex is accounted
        for; change the league size or the flex count and that moves. Deriving it
        from these numbers is why the model stays correct for any league shape.
    """
    slots = {}
    with st.container(horizontal=True):
        for position in LINEUP_SLOTS:
            slots[position] = st.number_input(
                position, min_value=0, max_value=6,
                value=int(current.get(position, 0)),
                key=f"{key_prefix}_slot_{position}",
            )
    return slots


def pick_label(team, round_number, num_teams, num_rounds, third_round_reversal):
    """Work out the ROUND.PICK label for one team's pick in one round.

    Shown beside each keeper row so you can see which pick is being spent
    without working the snake out in your head.

    Steps:
        1. Call `picks_for_slot` from draft_model/mechanics.py for every pick
           that team owns. Using it rather than fresh arithmetic keeps this
           label honest when third-round reversal is on.
        2. Take the entry for this round; rounds are 1-indexed, the list is not.
        3. Convert the overall number into a position within its round.

    Args:
        team: The team's draft slot, 1-indexed.
        round_number: Which round, 1-indexed.
        num_teams: League size.
        num_rounds: How many rounds are drafted.
        third_round_reversal: Whether round 3 repeats round 2's order.

    Returns:
        tuple: `(overall_pick, label)` — the absolute pick number, and a string
            such as "3.05" for display.
    """
    owned = picks_for_slot(team, num_teams, num_rounds, third_round_reversal)
    overall = owned[round_number - 1]
    in_round = (overall - 1) % num_teams + 1
    return overall, f"{round_number}.{in_round:02d}"


def keeper_editor(key_prefix, num_teams, num_rounds, third_round_reversal,
                  current, my_slot):
    """Draw one keeper row per team, and collect what was chosen.

    A keeper is a player a team holds instead of drafting someone new, and he
    costs that team a specific pick. So a keeper needs two answers per team: who,
    and in which round. The pick itself is derived from snake order rather than
    typed, because it is fully determined by those two.

    Steps:
        1. Load the id-to-name mapping, which is cached so this is cheap.
        2. Read whatever is already stored with `normalize_keepers` from
           draft_model/config.py, and index it by team so each row can seed
           itself.
        3. Warn about any stored keeper with no team or round yet — a leftover
           from when keepers were saved as a bare player list — naming the
           players so they can be re-entered rather than quietly lost.
        4. Draw a header row, then one row per team: the team, a round selector,
           a player selector, and the computed pick label.
        5. Mark your own team so it is obvious which row is yours.
        6. Collect every row that has a player chosen.

    Args:
        key_prefix: A prefix unique to this form, keeping the two tabs' widgets
            separate.
        num_teams: League size, which is also how many rows are drawn.
        num_rounds: How many rounds are drafted, bounding the round selector.
        third_round_reversal: Whether round 3 repeats round 2's order, which
            moves which pick a round costs.
        current: Whatever is stored on the draft under `keepers`, in any shape.
        my_slot: Your own draft position, marked in the table.

    Returns:
        list: One dictionary per chosen keeper, each with `team`, `round`, and
            `canonical_id`. A team with no player selected is simply absent, and
            the caller decides whether that is allowed.
    """
    names = player_name_options()   # {canonical_id: display_name}
    assigned, unassigned = normalize_keepers(current)
    by_team = {keeper.team: keeper for keeper in assigned}

    if unassigned:
        stale = ", ".join(names.get(cid, cid) for cid in unassigned)
        st.warning(
            f"These keepers were saved before keepers had rounds, so there is no "
            f"pick attached to them yet: **{stale}**. Assign each one a team and "
            f"a round below. Until then this draft cannot be simulated.",
            icon=":material/warning:",
        )

    header = st.columns([2, 2, 6, 2])
    for column, title in zip(header, ("Team", "Round", "Keeper", "Pick")):
        column.caption(title)

    # None sorts before the ids, so "no keeper" is always the first option.
    options = [None] + list(names)

    chosen = []
    for team in range(1, num_teams + 1):
        existing = by_team.get(team)
        row = st.columns([2, 2, 6, 2])

        row[0].write(f"**Team {team}**" + (" (you)" if team == my_slot else ""))

        round_number = row[1].selectbox(
            "Round", range(1, num_rounds + 1),
            index=(existing.round - 1) if existing and existing.round <= num_rounds else 0,
            key=f"{key_prefix}_kr_{team}", label_visibility="collapsed",
        )

        # A stored id that is no longer in the player list would make the widget
        # raise, so fall back to "no keeper" rather than trusting it blindly.
        default = existing.canonical_id if existing else None
        player = row[2].selectbox(
            "Keeper", options,
            index=options.index(default) if default in options else 0,
            format_func=lambda cid: names.get(cid, "— none —") if cid else "— none —",
            key=f"{key_prefix}_kp_{team}", label_visibility="collapsed",
        )

        _, label = pick_label(team, round_number, num_teams, num_rounds,
                              third_round_reversal)
        row[3].write(label if player else "—")

        if player:
            chosen.append({"team": team, "round": round_number,
                           "canonical_id": player})

    return chosen


def keeper_problems(keepers, num_teams, has_keepers):
    """List the reasons a keeper setup cannot be saved yet.

    Returned as messages rather than raised, so the form can show every problem
    at once and keep the save button disabled until they are all gone. The same
    rules are enforced again in DraftConfig, which is what actually protects the
    simulation; this is the friendly version.

    Steps:
        1. Return nothing at all when the league has no keepers, since there is
           nothing to check.
        2. Complain if any team has no keeper, naming the teams. A keeper league
           spends one pick per team, so a missing one is an unanswered question
           rather than a valid choice.
        3. Complain if the same player is kept by two teams.

    Args:
        keepers: The rows collected by `keeper_editor` above.
        num_teams: League size, so missing teams can be named.
        has_keepers: Whether this league uses keepers at all.

    Returns:
        list: Human-readable problems, empty when the setup is ready to save.
    """
    if not has_keepers:
        return []

    problems = []

    covered = {keeper["team"] for keeper in keepers}
    missing = [team for team in range(1, num_teams + 1) if team not in covered]
    if missing:
        problems.append(
            f"Every team needs a keeper in a keeper league. Still missing: "
            f"{', '.join(f'Team {team}' for team in missing)}."
        )

    seen, duplicated = set(), set()
    for keeper in keepers:
        if keeper["canonical_id"] in seen:
            duplicated.add(keeper["canonical_id"])
        seen.add(keeper["canonical_id"])
    if duplicated:
        names = player_name_options()
        listed = ", ".join(names.get(cid, cid) for cid in sorted(duplicated))
        problems.append(f"Kept by more than one team: {listed}.")

    return problems


st.title("Draft Manager")
st.caption("Create a new draft, or edit the settings of an existing one.")

edit_tab, new_tab = st.tabs(["Edit existing draft", "Create new draft"])

# ---------------------------------------------------------------------------
# Edit existing draft
# ---------------------------------------------------------------------------
with edit_tab:
    # list_drafts(): every saved draft doc -- draft_id, name, num_teams,
    # draft_position, num_rounds, platform, scoring_format (.value string).
    drafts = ctx.draft_service.list_drafts()

    if not drafts:
        st.info("No drafts yet. Use the 'Create new draft' tab to make one.")
    else:
        # Keyed by draft_id, not name: the id is stable across renames, so
        # renaming a draft can't orphan the picker's stored selection. Keying by
        # name meant a rename had to be patched up by writing to session_state
        # after the widget already existed, which Streamlit forbids.
        by_id = {d["draft_id"]: d for d in drafts}
        did = st.selectbox(
            "Draft to edit", list(by_id),
            format_func=lambda draft_id: by_id[draft_id]["name"],
            key="dm_edit_pick",
        )
        d = by_id[did]

        # Each widget is keyed by draft_id so switching drafts reseeds it from
        # that draft's saved values (value= only applies when a key is new).
        name = st.text_input("Draft name", value=d["name"], key=f"dm_name_{did}")
        num_teams = st.number_input("# Teams", 2, 32, value=d["num_teams"], key=f"dm_teams_{did}")
        # Draft position can't exceed the (live) team count; clamp the seed too.
        draft_position = st.number_input(
            "Draft position", 1, num_teams,
            value=min(d["draft_position"], num_teams), key=f"dm_pos_{did}",
        )
        num_rounds = st.number_input("Rounds", 1, 40, value=d["num_rounds"], key=f"dm_rounds_{did}")
        platform = st.selectbox(
            "Platform", PLATFORMS,
            index=PLATFORMS.index(d["platform"]) if d["platform"] in PLATFORMS else 0,
            key=f"dm_plat_{did}",
        )
        fmt = st.selectbox(
            "Scoring format", FORMATS,
            index=FORMATS.index(ScoringFormat(d["scoring_format"])),
            format_func=lambda f: f.value, key=f"dm_fmt_{did}",
        )

        st.divider()
        st.caption("Starting lineup")
        starting_slots = lineup_editor(f"dm_edit_{did}", d.get("starting_slots") or DEFAULT_STARTING_SLOTS)

        roster_size = st.number_input(
            "Roster size", 1, 40,
            value=int(d.get("roster_size") or num_rounds),
            key=f"dm_roster_{did}",
            help="Total slots including bench. Can exceed the number of rounds drafted.",
        )

        st.divider()
        has_keepers = st.checkbox(
            "This league has keepers", value=bool(d.get("has_keepers", d.get("keepers"))),
            key=f"dm_haskeep_{did}",
            help="Each team keeps one player, spending that round's pick on him.",
        )
        keepers = []
        if has_keepers:
            keepers = keeper_editor(f"dm_edit_{did}", num_teams, num_rounds,
                                    bool(d.get("third_round_reversal", False)),
                                    d.get("keepers") or [], draft_position)
        problems = keeper_problems(keepers, num_teams, has_keepers)
        for problem in problems:
            st.error(problem, icon=":material/error:")

        # Immediate feedback that the league shape is what you meant.
        st.caption(
            "Your picks: "
            + ", ".join(str(p) for p in picks_for_slot(draft_position, num_teams, num_rounds))
        )

        if st.button("Save changes", type="primary", icon=":material/save:", key="dm_save",
                     disabled=bool(problems)):
            ctx.draft_service.update_draft(
                did, name, num_teams, draft_position, num_rounds, platform, fmt.value,
                starting_slots=starting_slots, keepers=keepers, roster_size=roster_size,
                has_keepers=has_keepers,
            )
            # No need to fix up the picker after a rename -- it stores draft_id,
            # which doesn't change.
            st.toast("Draft settings saved.", icon=":material/check_circle:")
            st.rerun()

        # Danger zone: deleting is two-step so it can't be a fat-finger.
        with st.expander("Delete this draft"):
            st.warning(
                "Deleting a draft is permanent. Its saved markings, notes, and "
                "draft plan are left behind (not cascade-deleted)."
            )
            confirm = st.checkbox(
                "Yes, permanently delete this draft", key=f"dm_confirm_{did}"
            )
            if st.button(
                "Delete draft", type="primary", icon=":material/delete:",
                disabled=not confirm, key="dm_delete",
            ):
                deleted_name = d["name"]
                ctx.draft_service.delete_draft(did)
                # Drop the picker's stored selection so it doesn't point at a
                # now-missing draft_id on the next run.
                st.session_state.pop("dm_edit_pick", None)
                st.toast(f"Deleted draft '{deleted_name}'.", icon=":material/check_circle:")
                st.rerun()

# ---------------------------------------------------------------------------
# Create new draft
# ---------------------------------------------------------------------------
with new_tab:
    name = st.text_input(
        "Draft name", placeholder="ESPN Friends League 2026", key="dm_new_name"
    )
    num_teams = st.number_input("# Teams", 2, 32, 12, key="dm_new_teams")
    draft_position = st.number_input("Draft position", 1, num_teams, 1, key="dm_new_pos")
    num_rounds = st.number_input("Rounds", 1, 40, 15, key="dm_new_rounds")
    platform = st.selectbox("Platform", PLATFORMS, key="dm_new_plat")
    fmt = st.selectbox("Scoring format", FORMATS, format_func=lambda f: f.value, key="dm_new_fmt")

    st.divider()
    st.caption("Starting lineup")
    starting_slots = lineup_editor("dm_new", DEFAULT_STARTING_SLOTS)

    roster_size = st.number_input(
        "Roster size", 1, 40, value=int(num_rounds), key="dm_new_roster",
        help="Total slots including bench. Can exceed the number of rounds drafted.",
    )

    st.divider()
    has_keepers = st.checkbox(
        "This league has keepers", key="dm_new_haskeep",
        help="Each team keeps one player, spending that round's pick on him.",
    )
    keepers = []
    if has_keepers:
        keepers = keeper_editor("dm_new", num_teams, num_rounds, False, [],
                                draft_position)
    problems = keeper_problems(keepers, num_teams, has_keepers)
    for problem in problems:
        st.error(problem, icon=":material/error:")

    st.caption(
        "Your picks: "
        + ", ".join(str(p) for p in picks_for_slot(draft_position, num_teams, num_rounds))
    )

    # A name is required; keep the button disabled until one is entered, and
    # until every keeper question has an answer.
    if st.button(
        "Create draft", type="primary", icon=":material/add:",
        disabled=not name.strip() or bool(problems), key="dm_create",
    ):
        ctx.draft_service.create_draft(
            name.strip(), num_teams, draft_position, num_rounds, platform, fmt.value,
            starting_slots=starting_slots, keepers=keepers, roster_size=roster_size,
            has_keepers=has_keepers,
        )

        st.toast(f"Created draft '{name.strip()}'.", icon=":material/check_circle:")
        st.rerun()

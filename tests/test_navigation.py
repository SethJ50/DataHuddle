"""Tests that the sidebar matches the files on disk.

Streamlit does not check a page's path until somebody clicks it, so a typo in
`st.Page("pages/dfs_cheet_sheet.py")` is invisible until the moment it is not.
These tests read the navigation without running it and check it against the
directory, which is the one bug this file exists to catch.

`streamlit_app.py` is PARSED rather than imported. Importing it would call
`st.set_page_config`, warm the whole data context and start the app, none of
which belongs in a test suite.
"""

from unittest.mock import patch
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "pages"


def page_paths():
    """Collect every page path `streamlit_app.py` hands to `st.Page`.

    Steps:
        1. Parse the app entry point into a syntax tree.
        2. Walk it for every call to `st.Page`.
        3. Take each call's first argument, which is the path to the page file.

    Returns:
        list: Paths as written in the source, such as `"pages/home.py"`.
    """
    tree = ast.parse((ROOT / "streamlit_app.py").read_text())

    found = []
    for node in ast.walk(tree):
        is_page_call = (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "Page")
        if is_page_call and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant):
                found.append(first.value)
    return found


def page_variables():
    """Map each `page_thing = st.Page("pages/x.py")` name to the file it points at.

    The navigation dictionary at the bottom of `streamlit_app.py` refers to
    pages by VARIABLE NAME, not by path, so a test that wants to reason about
    which files end up public needs this translation first.

    Steps:
        1. Parse the app entry point into a syntax tree.
        2. Walk it for every assignment whose right-hand side is an `st.Page`
           call with a string first argument.
        3. Record the variable name on the left against that string.

    Returns:
        dict: Variable name to page path, such as
            `{"home_page": "pages/home.py"}`.
    """
    tree = ast.parse((ROOT / "streamlit_app.py").read_text())

    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        is_page_call = (isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Attribute)
                        and value.func.attr == "Page"
                        and value.args
                        and isinstance(value.args[0], ast.Constant))
        if isinstance(target, ast.Name) and is_page_call:
            found[target.id] = value.args[0].value
    return found


def unconditional_page_paths():
    """Collect the pages a deployment shows when no environment variable is set.

    This reads the FIRST top-level `sections = {...}` in `streamlit_app.py` --
    the one built before the `if pre_draft_enabled():` block adds to it -- which
    is exactly what a public Hugging Face Space ends up serving.

    Steps:
        1. Parse the app entry point into a syntax tree.
        2. Call `page_variables` above to learn which variable means which file.
        3. Find the first module-level assignment to `sections`. Module-level
           only, so the reassignment INSIDE the `if` is not what gets read.
        4. Translate every variable name mentioned in that dictionary back into
           a page path.

    Returns:
        set: Page paths visible without the environment variable, such as
            `{"pages/home.py", "pages/dfs_cheat_sheet.py"}`.
    """
    tree = ast.parse((ROOT / "streamlit_app.py").read_text())
    variables = page_variables()

    for node in tree.body:
        is_sections = (isinstance(node, ast.Assign)
                       and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id == "sections")
        if is_sections:
            names = [inner.id for inner in ast.walk(node.value)
                     if isinstance(inner, ast.Name)]
            return {variables[name] for name in names if name in variables}

    raise AssertionError("no top-level `sections = {...}` in streamlit_app.py")


# ---------------------------------------------------------------------------
# What a public deployment is allowed to show
# ---------------------------------------------------------------------------
# The Pre-Draft pages are personal tools and several of them WRITE to MongoDB.
# `deployment.pre_draft_enabled` keeps them off any machine that has not opted
# in. The gate is not the real security boundary -- a read-only database user
# is, see docs/DEPLOYMENT.md -- but it is the thing that decides what a visitor
# can even navigate to, so it is worth pinning down.

# Pages that create, delete or update records in MongoDB. Shipping any of these
# to a public deploy would let a stranger edit your drafts.
WRITE_PAGES = {
    "pages/draft_manager.py",
    "pages/draft_plan.py",
    "pages/draft_runner.py",
    "pages/player_profile.py",
    "pages/team_profile.py",
}


def test_no_write_page_is_visible_without_the_environment_variable():
    public = unconditional_page_paths()

    leaked = WRITE_PAGES & public
    assert not leaked, f"pages that write to MongoDB are public: {sorted(leaked)}"


def test_the_public_deploy_still_shows_home_and_the_dfs_pages():
    # The other direction: gating everything would leave an empty app.
    public = unconditional_page_paths()

    for page in ("pages/home.py", "pages/dfs_basic_plots.py",
                 "pages/dfs_player_profile.py", "pages/dfs_team_profile.py",
                 "pages/dfs_cheat_sheet.py"):
        assert page in public, f"{page} should be public but is gated"


def test_the_pre_draft_pages_are_hidden_unless_asked_for():
    # The whole point of the opt-in direction: an unconfigured machine, which
    # is what every deployment is, gets no Pre-Draft pages.
    from deployment import PRE_DRAFT_ENV_VAR, pre_draft_enabled

    with patch.dict("os.environ", {}, clear=True):
        assert pre_draft_enabled() is False

    with patch.dict("os.environ", {PRE_DRAFT_ENV_VAR: "1"}):
        assert pre_draft_enabled() is True


def test_switching_the_variable_off_actually_switches_it_off():
    # "0" and "false" are non-empty strings, and every non-empty string is
    # truthy in Python -- so a plain `if os.environ.get(...)` would read these
    # as a yes. This is the bug that check exists to prevent.
    from deployment import PRE_DRAFT_ENV_VAR, pre_draft_enabled

    for value in ("0", "false", "False", "no", "", "  "):
        with patch.dict("os.environ", {PRE_DRAFT_ENV_VAR: value}):
            assert pre_draft_enabled() is False, f"{value!r} should mean off"

    # ...while the obvious ways of saying yes all work, whitespace and case
    # included, because these get typed by hand into a shell profile.
    for value in ("1", "true", "TRUE", "Yes", " on "):
        with patch.dict("os.environ", {PRE_DRAFT_ENV_VAR: value}):
            assert pre_draft_enabled() is True, f"{value!r} should mean on"


def test_every_registered_page_exists():
    registered = page_paths()
    assert registered, "no st.Page calls found -- has the entry point moved?"

    missing = [path for path in registered if not (ROOT / path).is_file()]
    assert not missing, f"navigation points at files that do not exist: {missing}"


def test_every_page_file_is_registered():
    # The other direction: a page nobody can reach. Streamlit's older versions
    # auto-discovered anything under pages/, so an orphan used to appear by
    # itself; under st.navigation it simply never shows up.
    registered = {Path(path).name for path in page_paths()}
    on_disk = {path.name for path in PAGES.glob("*.py")
               if not path.name.startswith("__")}

    assert on_disk - registered == set(), "page files not in the sidebar"


def test_the_dfs_pages_are_registered():
    registered = {Path(path).name for path in page_paths()}
    for page in ("dfs_basic_plots.py", "dfs_player_profile.py",
                 "dfs_team_profile.py", "dfs_cheat_sheet.py"):
        assert page in registered


def test_page_files_all_compile():
    # A page with a syntax error also fails only when clicked.
    for path in PAGES.glob("*.py"):
        try:
            ast.parse(path.read_text())
        except SyntaxError as error:
            pytest.fail(f"{path.name} does not parse: {error}")


def test_the_two_halves_of_the_app_keep_separate_season_lists():
    # Daily Fantasy leans on play-by-play, which costs far more per season than
    # a game log does, so it loads fewer years. One shared list would quietly
    # make one half of the app wrong -- see the comment in streamlit_state.py.
    source = (ROOT / "streamlit_state.py").read_text()
    tree = ast.parse(source)

    seasons = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            name = node.targets[0].id
            if name in ("SEASONS", "DFS_SEASONS"):
                seasons[name] = [element.value for element in node.value.elts]

    assert set(seasons) == {"SEASONS", "DFS_SEASONS"}
    assert seasons["DFS_SEASONS"], "DFS needs at least one season"
    assert len(seasons["DFS_SEASONS"]) < len(seasons["SEASONS"])

    # Both must end at the same year, or the DFS pages would silently be a
    # season behind the rest of the app.
    assert max(seasons["DFS_SEASONS"]) == max(seasons["SEASONS"])
    assert seasons["DFS_SEASONS"] == sorted(seasons["DFS_SEASONS"])


# ---------------------------------------------------------------------------
# Selections have to outlive the widgets that show them
# ---------------------------------------------------------------------------
# Streamlit discards the state of any widget it did not draw on the latest run,
# so a per-page widget key is wiped the moment you navigate away. Anything the
# user expects to persist across pages therefore has to live in an ORDINARY
# session-state entry, which is never collected.


def test_the_chosen_draft_is_remembered_outside_any_widget():
    import ui_helpers

    # Not "<something>_sel": those are widget keys, and widget keys do not
    # survive a page change.
    assert not ui_helpers.SELECTED_DRAFT_KEY.endswith("_sel")


def test_draft_labels_stay_unique_when_two_leagues_share_a_name():
    # The picker used to key its options by NAME, so a duplicate silently hid
    # one league -- it could not be opened from any page.
    from ui_helpers import _draft_label, _duplicated_names

    drafts = [
        {"draft_id": "aaaaaaaa1111", "name": "Yahoo League 2026"},
        {"draft_id": "bbbbbbbb2222", "name": "Yahoo League 2026"},
        {"draft_id": "cccccccc3333", "name": "Home League"},
    ]
    duplicated = _duplicated_names(drafts)
    labels = [_draft_label(d, duplicated) for d in drafts]

    assert len(set(labels)) == len(drafts), f"labels collide: {labels}"
    assert labels[2] == "Home League", "a unique name should not be decorated"


def test_drafts_come_back_in_a_fixed_order():
    # Every page picks its league by position in this list, and MongoDB promises
    # no order at all for an unsorted query. An order that changed between runs
    # would silently switch which league you were looking at.
    from services.draft_service import DraftService

    docs = [
        {"draft_id": "c", "name": "C", "created_at": "2026-03-01"},
        {"draft_id": "a", "name": "A", "created_at": "2026-01-01"},
        {"draft_id": "b", "name": "B", "created_at": "2026-01-01"},
    ]

    service = DraftService.__new__(DraftService)
    with patch("services.draft_service.find_all", return_value=list(docs)):
        first = service.list_drafts()
    with patch("services.draft_service.find_all",
               return_value=list(reversed(docs))):
        second = service.list_drafts()

    assert [d["draft_id"] for d in first] == ["a", "b", "c"]
    assert first == second, "order must not depend on what Mongo happened to return"

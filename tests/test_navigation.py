"""Tests that the sidebar matches the files on disk.

Streamlit does not check a page's path until somebody clicks it, so a typo in
`st.Page("pages/dfs_cheet_sheet.py")` is invisible until the moment it is not.
These tests read the navigation without running it and check it against the
directory, which is the one bug this file exists to catch.

`streamlit_app.py` is PARSED rather than imported. Importing it would call
`st.set_page_config`, warm the whole data context and start the app, none of
which belongs in a test suite.
"""

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

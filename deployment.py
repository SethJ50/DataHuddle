"""Decides which parts of the app a given machine is allowed to show.

The Pre-Draft pages are personal tools. Several of them WRITE to MongoDB --
they create and delete drafts, save plans, and record markings -- so they have
no business appearing on a public deployment such as a Hugging Face Space.

The rule is one environment variable, and it is deliberately OPT-IN:

    unset  ->  Pre-Draft hidden   (what a server does, because you never
                                   configured anything there)
    "1"    ->  Pre-Draft shown    (what your laptop does, because you did)

That direction matters. A variable that HID the pages would mean forgetting to
set it once ships your draft tools to the internet; this way, forgetting only
costs you a page you can see locally anyway.

To turn the pages on locally, add this line to `~/.zshrc` next to the
`MONGODB_URI` export you already have, then open a new terminal:

    export DATAHUDDLE_PRE_DRAFT=1

Note:
    This is not a security boundary. It controls the NAVIGATION, and while
    Streamlit's `st.navigation` really does refuse to route to a page it was
    not given -- so a hidden page has no URL to visit -- the thing actually
    protecting your data on a public deploy is pointing `MONGODB_URI` at a
    READ-ONLY database user. See docs/DEPLOYMENT.md.
"""

import os

PRE_DRAFT_ENV_VAR = "DATAHUDDLE_PRE_DRAFT"
"""Name of the environment variable that unlocks the Pre-Draft pages. Set it on
your own machine; leave it unset everywhere the app is published."""

_TRUTHY = {"1", "true", "yes", "on"}
"""Values counted as "yes". Checked lowercased, so `TRUE` and `True` work too.
Anything else -- including "0", "false", and the empty string -- counts as no,
which is why this is a set membership test rather than a plain truthiness
check: in Python the STRING "0" is truthy, so `if os.environ.get(...)` would
read `DATAHUDDLE_PRE_DRAFT=0` as an enthusiastic yes."""


def pre_draft_enabled():
    """Say whether this machine should show the Pre-Draft pages.

    Called once by `streamlit_app.py` while it is deciding what to put in the
    sidebar. Your laptop answers True because you exported the variable in your
    shell profile; a deployment answers False because nobody set it there.

    Steps:
        1. Read the DATAHUDDLE_PRE_DRAFT environment variable, defaulting to
           the empty string when it is not set at all.
        2. Strip surrounding whitespace and lowercase it, so a stray space or a
           capital letter does not silently turn the pages off.
        3. Report whether the result is one of the accepted "yes" values.

    Returns:
        bool: True if the Pre-Draft pages should appear in the sidebar, False
            if the app should behave as a public, DFS-only deployment.
    """
    return os.environ.get(PRE_DRAFT_ENV_VAR, "").strip().lower() in _TRUTHY

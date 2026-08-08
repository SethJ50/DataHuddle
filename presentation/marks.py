"""Display metadata for player markings: headers, colours, widths, and scope.

registry.py owns the DOMAIN question -- which marks exist, and what they are
called when one is saved to the database. This file owns the LOOK, in one place,
so the draft plan board, the draft runner console and the team profile editor
cannot drift into showing the same six marks three different ways. They did
exactly that before this module became the single source.

TO CHANGE HOW A MARK APPEARS ANYWHERE IN THE APP, edit MARK_STYLE below and
nothing else.

Deliberately free of Streamlit, like every other module in presentation/. The
column-config helper hands back plain dictionaries of keyword arguments, and the
page turns those into widgets. That keeps this testable without a browser.
"""

from registry import MARKING_CATEGORIES

# Fallback widths in pixels for a mark column that does not name its own.
#
# TWO OF THEM, because an editable table needs more room than a read-only one.
# st.data_editor draws a column-type icon into every header, which competes with
# the heading for the same few pixels; st.dataframe draws no such icon. One width
# for both modes therefore has to be either too wide for the read-only tables or
# too cramped for the editable ones.
DEFAULT_MARK_WIDTH = 40
EDITABLE_MARK_WIDTH = 60

# Per-mark display config, keyed by the registry category name -- which is also
# the DataFrame column name every page uses, so a saved value round-trips
# without translation.
#
#   emoji    -> an emoji for this mark, where one says it clearly. Optional.
#               Doubles as `header` below when that is not set separately.
#   header   -> what a TABLE COLUMN heading shows. Optional; falls back to the
#               emoji, then to the category name. Set it explicitly only for a
#               mark that has no emoji and needs a short string instead.
#   color    -> background tint for the cell when the mark is checked.
#   width    -> width in pixels on a READ-ONLY table. Optional; falls back to
#               DEFAULT_MARK_WIDTH.
#   editable_width -> width in pixels on an EDITABLE table. Optional; falls back
#               to EDITABLE_MARK_WIDTH. Only worth setting for a mark whose
#               heading is unusually wide.
#   help     -> the hover tooltip. Optional, and defaults to the category name.
#               THIS IS WHAT MAKES AN EMOJI HEADER READABLE -- an unlabelled
#               icon is a guess until you hover it, so do not leave it off a
#               mark whose emoji is not obvious.
#   position -> restricts the mark to one position's tab. Absent means it shows
#               everywhere.
MARK_STYLE = {
    "Safe":   {"emoji": "👍", "color": "#3b82f6", "help": "Safe — a floor you can rely on."},
    "Upside": {"emoji": "📈", "color": "#eab308", "help": "Upside — a ceiling worth reaching for."},
    "Love":   {"emoji": "❤️", "color": "#22c55e", "help": "Love — you want him on your team."},
    "Like":   {"emoji": "✅", "color": "#86efac", "help": "Like — happy to take him at the right price."},
    "Uncertain Backfield": {
        "header": "BF?",                      # no emoji says this clearly
        "color": "#a855f7",
        "help": "Uncertain Backfield — the touches are not settled.",
        "position": "RB",
    },
    "New Top 12 Receiver": {
        "header": "T12",
        "color": "#a855f7",
        "help": "New Top 12 Receiver — a breakout into the top tier.",
        "position": "WR",
    },
}


# Derived colour map, keyed by COLUMN NAME (which is the category name). Handed
# straight to `highlight_true` in presentation/st_tables.py, which paints a cell
# when its checkbox is ticked.
MARK_COLORS = {
    category: spec["color"] for category, spec in MARK_STYLE.items()
}


def visible_marks(position=None):
    """List which marks belong on one position's table, in registry order.

    A couple of marks only make sense for one position — an uncertain backfield
    is not a thing a quarterback has — so a table asks which ones apply to it
    rather than showing all six everywhere.

    Steps:
        1. Walk MARKING_CATEGORIES from registry.py, so the order on screen is
           the order the domain list declares and never depends on this file.
        2. Keep a mark whose spec has no `position` at all, since that means it
           shows everywhere.
        3. Keep a position-scoped mark only when it matches the position asked
           for.

    Args:
        position: The position whose table this is, such as "RB". Pass None to
            get every mark, which is what a table not split by position — the
            draft runner console — wants.

    Returns:
        list: The category names to show, in registry order. These are also the
            DataFrame column names to build, so a caller can use the result for
            both without translating.
    """
    visible = []
    for category in MARKING_CATEGORIES:
        scope = MARK_STYLE.get(category, {}).get("position")
        if scope is None or position is None or scope == position:
            visible.append(category)
    return visible

def mark_emoji(category):
    """Get one mark's emoji, or an empty string when it has none.

    Some marks say what they are in a single symbol; "Uncertain Backfield" does
    not, and gets a short text heading instead. Anywhere the FULL name is already
    on screen, the emoji is decoration to sit beside it — and returning "" rather
    than a placeholder is what lets a caller add it unconditionally.

    Steps:
        1. Look the category up in MARK_STYLE and read its `emoji`.
        2. Return an empty string when there isn't one.

    Args:
        category: A marking category name.

    Returns:
        str: The emoji, or "" for a mark that has none.
    """
    return MARK_STYLE.get(category, {}).get("emoji", "")


def mark_header(category):
    """Get the short heading a TABLE COLUMN shows for one mark.

    A column is too narrow for "New Top 12 Receiver", so it shows a symbol or a
    short string instead, with `mark_help` supplying the meaning on hover.

    Steps:
        1. Use the explicit `header` when the mark sets one.
        2. Otherwise use its emoji, which is the heading for most marks.
        3. Otherwise fall back to the category name, so a mark nobody has styled
           yet still renders as something readable.

    Args:
        category: A marking category name.

    Returns:
        str: The heading to show, such as "👍" or "BF?".
    """
    spec = MARK_STYLE.get(category, {})
    return spec.get("header") or spec.get("emoji") or category


def mark_help(category):
    """Get the hover tooltip explaining one mark.

    An emoji heading is a guess until you hover it, so every mark carries a
    sentence saying what it means.

    Steps:
        1. Look the category up in MARK_STYLE and read its `help`.
        2. Fall back to the category name, which at least names the mark.

    Args:
        category: A marking category name.

    Returns:
        str: The tooltip text.
    """
    return MARK_STYLE.get(category, {}).get("help", category)

def mark_column_config(position=None, editable=False):
    """Build the display settings for each mark column, ready for Streamlit.

    Returns plain dictionaries rather than widgets, so this module never has to
    import Streamlit. The page turns each one into a checkbox column with a
    single `**` unpack, which is what keeps every page's marks identical without
    any of them holding a copy of the settings.

    Steps:
        1. Ask `visible_marks` above which marks apply to this position.
        2. Choose which width applies: the editable preset for a table the user
           can type into, the read-only one otherwise. See `editable` below for
           why the two differ.
        3. For each mark, read its heading, tooltip and width out of MARK_STYLE,
           falling back to the category name and the chosen preset so a newly
           added mark renders sensibly before anyone has styled it.

    Args:
        position: The position whose table this is, or None for all marks.
        editable: True when these columns are going into an `st.data_editor`,
            False for a read-only `st.dataframe`. It only changes the WIDTH.
            An editable table draws a column-type icon inside every header,
            which crowds out the heading at the narrow read-only width, so the
            editable preset is deliberately wider.

    Returns:
        dict: Maps each column name to the keyword arguments for
            `st.column_config.CheckboxColumn` — `label`, `help`, and `width`.
            Use it as:

                for column, settings in mark_column_config("RB").items():
                    column_config[column] = st.column_config.CheckboxColumn(**settings)
    """
    # Which override key and which fallback apply, decided once rather than
    # inside the loop.
    width_key = "editable_width" if editable else "width"
    fallback = EDITABLE_MARK_WIDTH if editable else DEFAULT_MARK_WIDTH

    settings = {}
    for category in visible_marks(position):
        spec = MARK_STYLE.get(category, {})
        settings[category] = {
            "label": mark_header(category),
            "help": mark_help(category),
            "width": spec.get(width_key, fallback),
        }
    return settings

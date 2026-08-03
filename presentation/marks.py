"""Display metadata for player markings (labels, colors, position scope).

Kept separate from registry.py (which owns the domain list MARKING_CATEGORIES)
so that any page showing marks -- draft plan, player profile, team profile --
renders them with the SAME short labels and colors from one source of truth.
"""

# Per-mark display config, keyed by the registry category name.
#   label    -> short column header shown on the board
#   color    -> background color for the cell when the mark is checked
#   position -> (optional) restricts the mark to one position's tab;
#               absent means the mark shows on every tab
CATEGORY_STYLE = {
    "Safe":                {"label": "Safe",   "color": "#3b82f6"},                    # blue
    "Upside":              {"label": "Upside", "color": "#eab308"},                    # yellow
    "Love":                {"label": "Love",   "color": "#22c55e"},                    # green
    "Like":                {"label": "Like",   "color": "#86efac"},                    # lighter green
    "Uncertain Backfield": {"label": "UBF",    "color": "#a855f7", "position": "RB"},  # purple
    "New Top 12 Receiver": {"label": "T12",    "color": "#a855f7", "position": "WR"},  # purple
}

# Derived label -> color map. Board columns are named by their label, so the
# conditional-formatting helper looks colors up by label rather than full name.
MARK_COLOR_BY_LABEL = {spec["label"]: spec["color"] for spec in CATEGORY_STYLE.values()}

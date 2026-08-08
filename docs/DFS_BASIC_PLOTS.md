# Basic Plots — how the page is wired

A map of [pages/dfs_basic_plots.py](../pages/dfs_basic_plots.py) and everything
behind it, for the next time somebody adds a plot.

**The page knows about no particular plot.** It reads
[presentation/dfs_plot_registry.py](../presentation/dfs_plot_registry.py), draws
the filters each entry asks for, calls that entry's builder and chart, and
renders whatever comes back. Adding a plot is one entry in `PLOTS`; the page
itself never changes.

---

## The whole path

```mermaid
flowchart TD
    subgraph sources["nflreadpy (network, cached once per process)"]
        pbp[("load_pbp<br/>372 MB/season raw")]
        ffo[("load_ff_opportunity<br/>expected points")]
    end

    subgraph repo["repositories/dfs_read_repo.py"]
        prune["DfsReadRepo<br/><i>prunes pbp to 44 columns in Polars<br/>BEFORE to_pandas → 19 MB</i>"]
    end

    subgraph services["services/ — one question each"]
        scoring["dfs_scoring.rescore<br/><i>PPR → FanDuel / DraftKings</i>"]
        opp["dfs_opportunity_service<br/>actual_vs_expected"]
        team["dfs_team_service<br/>offensive_tendencies<br/>defensive_allowances"]
    end

    subgraph registry["presentation/dfs_plot_registry.py"]
        plots["PLOTS — one PlotSpec per plot<br/><i>label · filters · build · chart</i>"]
    end

    subgraph charts["presentation/dfs_charts.py"]
        alt["Altair specs<br/><i>one dataset per chart</i>"]
    end

    page["pages/dfs_basic_plots.py<br/><i>filters → build → chart → table</i>"]

    pbp --> prune
    ffo --> prune
    prune --> opp
    prune --> team
    scoring --> opp
    scoring --> team
    opp --> plots
    team --> plots
    plots --> page
    alt --> page
    page --> ui["st.altair_chart(theme=None)<br/>+ 'The numbers' expander"]
```

---

## What the reader does, and what happens

```mermaid
sequenceDiagram
    actor You
    participant Page as dfs_basic_plots.py
    participant Reg as PLOTS_BY_LABEL
    participant Svc as service (build)
    participant Chart as chart function

    You->>Page: pick a plot
    Page->>Reg: look the label up
    Reg-->>Page: PlotSpec
    Note over Page: draw ONLY the filters<br/>this spec asked for

    You->>Page: set season / weeks / positions …
    Page->>Page: split the filter values three ways
    Page->>Svc: build(repo, **for_build)
    Svc-->>Page: a small tidy frame

    alt frame is empty
        Page-->>You: "Nothing matches those filters."
    else
        Page->>Chart: chart(frame, **for_chart)
        Chart-->>Page: Altair spec
        Page-->>You: chart + reading note + numbers table
    end
```

---

## The filter routing

The one part of the page with real logic. A filter names either the DATA to
fetch, the way to DRAW it, or both.

```mermaid
flowchart LR
    sel["selected{}<br/><i>what the widgets returned</i>"]

    sel --> q{"in<br/>chart_filters?"}
    q -- yes --> chartonly["for_chart only"]
    q -- no --> build["for_build"]

    sel --> s{"in<br/>shared_filters?"}
    s -- yes --> both["for_chart AS WELL"]

    build --> bcall["plot.build(repo, …)"]
    chartonly --> ccall["plot.chart(frame, …)"]
    both --> ccall
    both -.-> bcall

    scoring{"uses_scoring?"} -- yes --> bcall
    scoring -- no --> hidden["contest toggle hidden"]
```

`split` is the reason `shared_filters` exists: the builder uses it to pick which
columns to read, and the chart uses it to title the axes.

`uses_scoring` is false for the pass-rate plot — a pass rate is the same number
whatever a catch pays, so offering the choice would imply otherwise.

**Both routes are tested.** `test_every_filter_reaches_the_function_that_accepts_it`
in [tests/test_dfs_opportunity.py](../tests/test_dfs_opportunity.py) checks every
declared filter against the signature of the function it is routed to — a
mis-route otherwise raises an unexpected-keyword error only at click time.

---

## The four plots

```mermaid
flowchart TD
    subgraph p1["Actual points vs expected (xFP)"]
        b1["actual_vs_expected"] --> c1["actual_vs_expected_chart<br/><i>scatter + red y = x</i>"]
    end
    subgraph p2["Neutral-script pass rate by team"]
        b2["offensive_tendencies"] --> c2["team_tendency_chart<br/><i>32 sorted bars</i>"]
    end
    subgraph p3["Rushing: FP vs EPA allowed"]
        b3["_rush_defence"] --> c3["defensive_allowance_chart<br/><i>team abbreviations + crosshair</i>"]
    end
    subgraph p4["Passing: FP vs EPA allowed"]
        b4["_pass_defence"] --> c4["_pass_defence_chart"]
    end

    ffo2[("ff_opportunity")] --> b1
    pbp2[("pbp")] --> b2
    pbp2 --> b3
    pbp2 --> b4
    ffo2 --> b3
    ffo2 --> b4
```

| Plot | Filters | Scoring? | Sources |
|---|---|---|---|
| Actual vs xFP | season, weeks, positions, split, min games | yes | `ff_opportunity` |
| Neutral pass rate | season, weeks, measure | **no** | `pbp` |
| Rushing defence | season, weeks, positions | yes | `pbp` + `ff_opportunity` |
| Passing defence | season, weeks, positions | yes | `pbp` + `ff_opportunity` |

The two defence plots are the same function with `play_kind` fixed, wrapped so
the registry stays declarative rather than carrying partials.

---

## Two rules that fail silently if broken

### One dataset per chart

Streamlit sends exactly **one** dataset to the browser per chart, whatever the
chart thinks it has. A reference line built from its own little table is dropped
on the way out, and the layer is left pointing at a dataset name that no longer
exists — no error, nothing drawn.

```mermaid
flowchart LR
    subgraph bad["✗ separate dataset"]
        d1[("points df")] --> l1["layer 0"]
        d2[("2-point line df")] --> l2["layer 1"]
        l2 -.->|"Streamlit drops it"| gone["layer draws nothing"]
    end
    subgraph good["✓ same dataframe"]
        d3[("points df")] --> l3["layer 0 — circles"]
        d3 --> l4["layer 1 — line<br/><i>y encoded against the x FIELD</i>"]
    end
```

A `y = x` line needs no data of its own: encode the y channel against the x
field and every row contributes a point on the diagonal. Written up as
`EVERY_LAYER_SHARES_THE_DATA` at the top of
[presentation/dfs_charts.py](../presentation/dfs_charts.py).

### `theme=None`

`st.altair_chart` defaults to `theme="streamlit"`, which restyles the chart —
and controlling colour exactly is the only reason these pages use Altair rather
than `st.scatter_chart`.

---

## Adding a plot

```mermaid
flowchart LR
    a["1 · write a build function<br/><i>in services/, returns a tidy frame</i>"]
    b["2 · write a chart function<br/><i>in dfs_charts.py, ONE dataset</i>"]
    c["3 · add a PlotSpec to PLOTS<br/><i>label · filters · build · chart</i>"]
    d["4 · new filter type?<br/>add a branch in the page loop"]
    a --> b --> c --> d
    c -.->|"otherwise nothing<br/>in the page changes"| done(["appears in the dropdown"])
```

Steps 1–3 are the normal case. Step 4 is only needed for a filter the page has
never drawn before; the existing six — `season`, `weeks`, `positions`, `split`,
`measure`, `minimum_games` — already cover a lot.

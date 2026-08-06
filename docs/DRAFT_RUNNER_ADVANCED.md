# Draft Runner — Advanced Additions

Three features: **Positional Suggestion**, **Team Relative Strengths**, and the
**Cliff Finder**. Written in plain English, with the open design questions
answered and the evidence for each answer, so this can be turned into a phased
build plan.

Everything here was tested against the real simulator and the real UDK data
before being recommended. Where a measurement changed my advice, the measurement
is shown.

---

# Part 1 — The design questions, answered

## 1.1 How do we compare teams fairly when rosters are half-built?

**Your question:** mid-draft, nobody's starting lineup is full. Comparing raw
totals punishes whoever picks late and makes early-round comparisons meaningless.
You wanted a way to "mock fill in the rest of each roster".

**The answer: we already have it, and it costs almost nothing.**

Every pick, the Runner re-simulates the entire rest of the draft. That matrix
records where *every* player goes in *every* simulation — not just yours. Since
each team's pick numbers are known, we can read off what every team ends up with
in every simulation, score their lineups, and average.

Measured cost: the simulation itself is ~190ms and **we already pay it**.
Deriving all twelve teams' rosters on top adds about **50ms**. That is the whole
"mock fill" problem solved as a by-product of work already being done.

**Recommendation: offer two modes behind a toggle**, because they answer
different questions and both are cheap:

| Mode | What it means | Empty slots |
|---|---|---|
| **Projected final** *(default)* | Where each team is heading | filled by simulating the rest of the draft |
| **As drafted** | What each team has actually got | filled at replacement level |

Default to **Projected final** — it is the direct answer to your question, and it
is the only one that is fair in round 3.

**One honest caveat.** Early in the draft, most of a team's projected final
roster is simulated rather than real, so the numbers will cluster and mostly
reflect draft slot. That is not a flaw — it is true — but it means the panel is a
weak feedback signal in round 2 and a strong one by round 8. Showing both modes
lets you see that for yourself rather than being misled by one.

## 1.2 How do we make Risk and Upside mean something?

**Your instinct was right, and the data backs it.** You suspected upside mostly
restates the projection. Measured within each position:

| | QB | RB | WR | TE |
|---|---|---|---|---|
| **Upside** vs projected points | +0.60 | **+0.93** | +0.89 | +0.82 |
| **Risk** vs projected points | **−0.63** | −0.12 | −0.16 | −0.05 |

Upside is almost the same number as the projection, especially at running back.
A column showing raw upside would be a second, blurrier copy of `Proj`.

**Correction to something I told you earlier.** I previously said risk was
orthogonal to points, based on a pooled correlation of −0.11. That pooled figure
hid the truth: **quarterback risk is strongly correlated (−0.63)**, the other
positions are not. So risk needs the same treatment as upside, at least at QB.

### The fix: measure each player against players projected like him

For each position, fit the simple relationship between projection and the metric,
and keep only what is left over. A player then scores well not because he is good,
but because he has **more upside than a player projected like him usually has**.

Verified: after this adjustment, the correlation with projected points is
**exactly 0.00** for both metrics at every position. All the redundancy is gone
and only the genuinely new information remains.

**Recommendation: adjust both metrics, within position, using one rule.** No
special-casing — QB risk needs it, and applying it everywhere costs nothing and
keeps the metric comparable across positions.

Read the adjusted numbers as: *positive upside = more explosive than his
projection suggests; positive risk = shakier than his projection suggests.*

### The aggregation problem

**Your objection was exactly right**: averaging cancels (one boom and one bust
average to neutral, hiding both), and summing depends on how many players you
happen to have.

The answer is that **risk and upside want different aggregations, because they
are asked for different reasons.**

**Upside — use the best one or two, not the average.** The question is "do I have
a ceiling here?" One genuine lottery ticket is what you want; five mediocre
players are not equivalent to one explosive one. Taking the maximum (or the mean
of the top two, which is steadier) answers that directly, never cancels, and does
not care how many players you own. **This solves both of your objections at
once.**

**Risk — use a projection-weighted average across starters.** The question here
is different: "how exposed am I?" Exposure is about how many *points* sit behind
shaky players, so weight each player's risk by his projection. A shaky WR3 barely
matters; a shaky RB1 matters enormously. An unweighted average treats them the
same.

**Bench risk: consider not showing it.** A bench is where fliers belong — high
risk there is a feature, not a problem, and a metric that penalises it is
actively misleading. Bench *upside* is the interesting half.

Summary of the rule:

| Where | Upside | Risk |
|---|---|---|
| Starters | best 1–2 | projection-weighted average |
| Bench | best 1–2 | *probably omit* |

## 1.3 The Cliff Finder — what it actually gives you

**Your note:** *"I am interested but can't fully comprehend what this would give
me."* Fair — here it is on your real data, top of the board, nobody drafted:

```
RB:  368 -> 361 -> 345 -> 299 -> 282 -> 279 -> 272 -> 259
     3 RBs left before a 46-point drop

TE:  262 -> 259 -> 221 -> 212 -> 194 -> 186 -> 182 -> 178
     2 TEs left before a 38-point drop

WR:  343 -> 339 -> 321 -> 300 -> 293 -> 271 -> 264 -> 256
     5 WRs left before a 22-point drop

QB:  428 -> 398 -> 396 -> 387 -> 384 -> 378 -> 376 -> 375
     1 QB left before a 30-point drop
```

**That is the whole feature: a countdown and a drop size, per position.**

Look at what it tells you at a glance. There are **two** elite tight ends and then
a 38-point cliff. There are **five** receivers before a much gentler 22-point
step. So if seven picks happen before your turn, the tight end situation is
urgent and the receiver situation is not — and you can see that without doing any
arithmetic.

**Why it is not a duplicate of Cost of Waiting.** Cost of waiting gives you a
*price* — "passing costs about 15 points". The cliff gives you the *reason and the
count* — "because there are only 2 left before a 38-point drop". One is a number
to weigh; the other is a countdown you can act on.

**And it covers a real blind spot.** I tested what the simulation actually
responds to (see 1.4 below): the simulator drafts by **ADP**, not by projections.
So a projection cliff that the market has not yet priced in is **invisible** to
every simulation-based number in the app. The Cliff Finder reads the projections
directly, so it is the only thing that would catch it.

It also needs no simulation at all — it is a sort and a subtraction over
available players. **Cheapest feature here, and the most legible.**

## 1.4 What I learned about the Positional Suggestion

I built and measured this before recommending it, and the findings should shape
how it is presented.

**Finding 1 — the differences are small.** Simulating "take the best RB" against
"take the best WR" and playing out the rest, the gap between the best and worst
choice was about **6–7 points out of ~1790** — under half a percent. It is
statistically real at 4,000 simulations (3–4× the standard error) but it is not
large.

**Finding 2 — a single draft can easily go the other way.** The spread across
simulations is about **±85 points**, more than ten times the gap. The average
favours one choice; any individual draft frequently does not.

**Finding 3 — your future self corrects, which is why the gaps are small.** I
made running backs twice as scarce and the recommendation barely moved (+7.4 →
+7.2). The reason is that the simulator models positional need: skip a running
back now and your simulated future self reaches for one later. The hole gets
filled either way. What differs is only the *quality* of who fills it — which is
genuinely a smaller effect than intuition suggests.

**Finding 4 — it follows the market, not the projections.** Changing projections
without changing ADP did not move the answer at all. The simulation decides *who
you get* from ADP and only then scores them. So this feature answers "given how
the market will behave, which choice leaves me better off" — a good question, but
not "which position is undervalued".

**What this means for the build.** Build it, but present it honestly:

- Show the **gap and its margin of error**, never a bare ranking.
- When options overlap, **say "too close to call"** rather than ordering them. A
  tool that confidently picks between two indistinguishable options is worse than
  one that admits they are the same.
- Use **more simulations than the console's 1,000** — 4,000 or so — because the
  effect being measured is small.
- Treat "these are all close" as a **useful answer**: it means stop agonising and
  take the best player available.

---

# Part 2 — The features

## 2.1 Positional Suggestion

**What it shows.** For each position you could take right now, the projected
total of your final starting lineup if you took the best available player there
and drafted normally afterwards — with an error margin, and an explicit "too
close to call" when options overlap.

**How it works.** Hypothetically mark the best available player at that position
as taken, simulate the remaining draft several thousand times, work out which
players you end up with in each simulation, slot them into your starting lineup
and total the projections. Repeat per position, compare.

**Built from:** the existing simulator, the existing roster-slotting used by the
roster panel, and one new piece — reading a team's final roster out of a picks
matrix.

**Cost:** roughly 190ms per position at 1,000 simulations, so about 1 second for
six positions at that size and ~4 seconds at the recommended 4,000. **This is a
button you press, not something that recalculates as you type.**

**Depends on:** the "read a team's roster out of a picks matrix" helper, which
Team Relative Strengths also needs. Build that once, shared.

**Watch out for:** implying precision. See 1.4.

## 2.2 Team Relative Strengths

A panel comparing all teams, with two views as you specified.

**My Team view** — one row per category, showing where you rank out of the number
of teams. Scannable: you should be able to see "1st at WR, 11th at TE" in a
glance.

**Category view** — pick a category from a dropdown, get a `Team | Value` table
ranked, with your row highlighted.

Both views read from the same underlying table, so this is one calculation and
two presentations.

### Categories

**Starting Lineup Total Strength** — total projected points of the starting
lineup, excluding K and DST. Those two have no projections in this app, so
including them would add zero to every team and just make the number look
precise without being it.

**Starting Positional Strength** — the same total broken out by QB, RB, WR, TE
and FLEX. This is where "1st overall but 11th at tight end" becomes visible.

**Bench Positional Strength** — average projected points of bench players at each
of QB, RB, WR, TE. Average is right here (unlike for upside) because the question
is "what is my typical fallback worth", and that genuinely is an average.

**Replacements** — your idea, and a good one: worst starter minus best bencher, at
QB / RB / WR / TE / FLEX. Lower means deeper.

This measures something VORP structurally cannot. VORP compares a player to what
is *freely available in the league*; this compares him to *what you already own*.
Late in a draft those are very different questions, and this is the one that
decides whether your next pick should be a starter upgrade or insurance.

Note it is always zero or positive: the roster panel slots greedily by
projection, so the best bencher is never better than the worst starter. It is a
depth measure, never a lineup error.

**Risk & Upside** — three groupings, using the adjusted metrics and the
aggregation rules from 1.2:

- *Starting Lineup Risk & Upside* — one risk number and one upside number for the
  whole lineup, excluding K and DST.
- *Starting Positional Risk & Upside* — the same per position.
- *Bench Positional Upside* — per position. Consider omitting bench risk entirely
  (see 1.2).

### Fill mode

The toggle from 1.1 — **Projected final** or **As drafted** — applies to every
category. Label it prominently: the same category means two quite different
things under the two modes, and a reader who misses the toggle will misread the
panel.

**Cost:** about 50ms on top of the simulation already being run each pick, in
Projected-final mode. Effectively free in As-drafted mode.

## 2.3 Cliff Finder

**What it shows.** Per position, among players still available: how many are left
before the next significant drop, and how big that drop is. "3 RBs left before a
46-point drop."

**Built from:** the projections of available players, sorted. No simulation, no
new data.

**Cost:** negligible.

**One decision to make:** how to define "the cliff". Simplest is the largest
single drop within the top handful of available players at that position. A more
stable alternative is the largest drop relative to the typical gap at that
position, which avoids calling a normal step a cliff when the position is thin.
Start with the simple version and see whether it misfires.

**Where to put it:** it belongs beside the cost-of-waiting chart, since they
answer the same question from two directions — price and countdown.

---

# Part 3 — Suggested phases

Ordered so that each phase produces something usable, and shared machinery gets
built once.

**Phase A — Cliff Finder.** No dependencies, no simulation, immediately useful,
and it covers the blind spot identified in 1.4. Good first phase because it is
small enough to finish and see working.

**Phase B — "Read a team's roster from a picks matrix".** Pure logic, fully
testable, no interface. Both remaining features need it. Small, but doing it as
its own phase keeps it clean and tested.

**Phase C — Team Relative Strengths, structure only.** The panel, the two views,
the toggle, and the three straightforward categories — total, positional, bench,
replacements. Leave risk and upside out. This is the biggest chunk of interface
work and it is worth having it settled before adding the subtler metrics.

**Phase D — Adjusted Risk & Upside.** The within-position adjustment and the
aggregation rules, then wire them into the panel built in Phase C. Separated
because the metric design is the fiddly part and deserves its own tests, and
because the panel is useful without it.

**Phase E — Positional Suggestion.** Last, because it is the most expensive, the
most easily misread, and the one whose value is least certain. By this point
Phase B has given it the roster-scoring helper for free.

A reasonable stopping point is after Phase C or D — those give you the comparison
panel you asked for, and the Cliff Finder covers the in-the-moment decision. Phase
E is worth doing, but its honest output will often be "these are close".

---

# Part 4 — What to be careful about

**The console is already dense** — 14 columns. Every feature here adds something
else to look at. Nine numbers per player does not help you decide faster; it helps
you hesitate. As these land, be willing to remove things that turned out not to
change a decision.

**Two of these features are near-free and one is not.** The Cliff Finder and the
team panel ride on work already being done. The Positional Suggestion needs its
own simulations and is the one that could make the page feel slow. Keep it behind
a button.

**Say what mode the numbers are in.** The fill-mode toggle changes what every
number in the team panel means. Label it where it cannot be missed.

**Do not let small differences look decisive.** This applies to the Positional
Suggestion most, but also to team rankings early in a draft, where the gaps are
mostly draft slot rather than skill.

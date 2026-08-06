# Draft Runner — Ideas

Your ideas fleshed out, plus ten new ones. Written in plain English: what each
thing is, why it would help on draft day, what it would be built from, and how
much work it is. No code — this is for deciding what is worth building.

---

## What makes an idea fit this system

Three capabilities the Draft Runner already has, and which most of these ideas
lean on. Worth reading first, because they explain why some ideas are nearly free
and others are real work.

**1. The simulation runs from wherever the board actually is.** Every pick, the
runner re-simulates the entire rest of the draft in about 190ms. That is not a
lookup against a pre-computed table — it is a fresh answer to "given exactly this
board, what happens next?"

**2. The full picks matrix survives, so JOINT questions are answerable.** Most
tools collapse a simulation into one number per player: "72% available". That
throws away the thing that matters. "Will *at least one* of these four backs get
back to me?" cannot be rebuilt from four separate percentages, because those
players compete for the same picks — one going early is precisely what lets
another survive. We keep the whole matrix, so questions of that shape are
answerable. **Several helpers for this are already written and unused**:
`prob_any_available`, `prob_all_available`, `simulated_pick_distribution`,
`pick_percentiles`.

**3. We can simulate forward from a hypothetical.** Because the simulator takes
"who is already drafted" as an input, we can ask: *if I take this player now,
what does the rest of the draft look like?* That is the basis for anything that
answers "what should I do" rather than "what is happening".

One consequence worth stating early: when we simulate forward, the model drafts
for **every** team including yours. So a lookahead really answers "if I take X
now and then draft sensibly from here". That is a reasonable proxy and it needs
no model of your strategy — but it is an assumption, not a fact.

**What we can also read:** each player carries a projection, an ADP target and
its spread, a tier, an injury **risk** and **upside** score, a **bye week**, and
his NFL **team**. Those last four are currently unused by the Runner and quietly
unlock several ideas below.

---

# Part 1 — Your ideas

## 1.1 Positional suggestion from simulating the rest of the draft

There are two very different things this could mean, and the difference matters.

**The shallow version** is ranking positions by the cost-of-waiting number the
Runner already shows. That is not really a suggestion — it is the chart you are
already looking at, sorted.

**The version worth building** is a genuine lookahead. For each position you
could take right now, hypothetically make that pick, simulate the rest of the
draft a thousand times, and score the starting lineup you end up with each time.
Then compare. The output is a short table:

> Take a **RB** now → your final starting lineup projects to **1,512** points
> Take a **WR** now → **1,498**
> Take a **TE** now → **1,486**

That answers the actual question — not "which position is scarce" but "which
choice leaves me with the better team".

**Why it helps.** Scarcity and value pull in different directions all the time.
Running back may be thinning fast while the best available player is a receiver.
Cost-of-waiting tells you about the thinning; it cannot tell you whether reacting
to it leaves you better off. This can.

**What it is built from.** The existing simulator, plus two small pieces: work
out which players your team ended up with in each simulation (your pick numbers
are known, so this is a lookup), then slot those players into your starting
lineup and total them. The roster-slotting logic already exists for the roster
panel.

**Effort: medium.** The scoring machinery is small. The real work is presenting
it honestly — see the caveat.

**Cost: about 190ms per candidate**, so roughly a second for six positions. This
has to be a button you press, not something that recomputes every time you type
in the search box.

**The caveat, and it is important.** The differences will often be small — a
handful of points out of 1,500, which is well inside the noise of the projections
themselves. Presenting "1,512 vs 1,498" as a recommendation implies a precision
that is not there. Show the *spread* alongside the average, and when two options
overlap, say they are level rather than ranking them. A tool that confidently
picks between two indistinguishable options is worse than one that says "these
are the same, take the player you like".

## 1.2 Team relative strengths

A panel comparing all twelve teams, so you know where you actually stand rather
than guessing. Everything here is computable from the pick log plus the player
table — no new simulation needed, so it is cheap and can update every pick.

### Starting roster projections

For each team, slot their drafted players into the league's starting lineup and
total the projections. Rank the teams.

**Why it helps.** It reframes every decision. "I need a tight end" is a hunch;
"I am 9th of 12 and the gap is almost entirely at tight end" is a plan. It also
catches the classic mistake of hoarding depth at a position where your starters
were already fine.

**Effort: small.** The slotting logic exists. The only new work is doing it for
all twelve teams instead of the one you are looking at.

**Caveat.** Only counts players in the model pool. Kickers, defenses and anyone
recorded as an unlisted pick have no projection, so a team's total is really
"their projected skill-position starters". Say so in the label, or people will
read a low number as a bad team when it may just be an unmodelled roster.

### Internal replacement — worst starter minus best bencher

Your phrasing, and it is a genuinely good measure. For the RB/WR/FLEX group, take
the worst player currently in a starting slot and the best player currently on
the bench, and look at the gap.

- **Small gap** — deep team. Losing a starter costs you little.
- **Large gap** — top-heavy. One injury or bye week and you fall off a cliff.

**Why it helps.** It measures something VORP does not. VORP compares a player to
what is *freely available in the league*; this compares him to *what you already
own*. Late in a draft those are very different questions, and this one is what
decides whether your next pick should be a starter upgrade or insurance.

**Why it is always a positive number**, which is worth knowing: the roster panel
slots greedily by projection, so the best bencher is by construction no better
than the worst starter. The gap is a depth measure, never a lineup error.

**Effort: small.** Falls straight out of the slotting that already happens.

**Extension worth considering:** do the same per position rather than only for
the flex group. "Your RB gap is 40 points, your WR gap is 4" tells you exactly
where you are fragile.

### Risk and upside, by position and overall

Every ranked player carries a **risk** score and an **upside** score from UDK.
Neither is used anywhere in the app right now. Roll them up per team: average
risk and average upside across the starting lineup, per position and overall.

**Why it helps.** Two teams can project identically and be completely different
propositions — one built from safe floors, the other from boom-or-bust. Knowing
which you are building matters late, when you should be deliberately correcting:
if you are already the highest-risk team in the league, take the safe running
back over the lottery ticket.

**How to show it.** A scatter with risk on one axis and upside on the other, one
dot per team, yours highlighted. Where you sit relative to the pack is the whole
message, and a scatter says that instantly where a table would not.

**Effort: small to medium.** The data is a join away. The chart is new but sits
alongside the cost-of-waiting chart we already have.

**Caveat.** These are UDK's opinions on a scale we do not control or document.
Treat them as a relative ranking within this data source, not an absolute
measure, and do not mix them into a points calculation as if they were.

### Other ideas for this panel

**Bye week collisions.** Every player carries a bye week, and this is currently
unused. Count how many of a team's *starters* share a bye. Three starters out in
week 9 is a real problem you can still fix in round 11, and nothing in the app
surfaces it today. **Cheap, concrete, and genuinely actionable** — probably the
highest value-per-effort item in this whole document.

**Unfilled starting slots.** Simply: which teams still have an empty starting
slot, and where. Tells you who is *forced* to take a position soon, which is much
stronger information than guessing at their preferences.

**Positional rank within the league.** Rather than one overall ranking, rank each
team at each position. "You are 1st at WR and 11th at TE" is far more useful than
"you are 6th overall".

---

# Part 2 — Ten new ideas

## 2.1 Positional cliff finder

For each position, look down the list of players still available and find the
next big drop in projection. Report it as "the next 3 receivers are worth about
the same, then there is a 22-point cliff".

**Why it helps.** This is the single most common in-draft question and the app
answers it nowhere. It converts vague scarcity into a countdown: if there are
three players left before the cliff and five picks before your turn, you know
exactly how worried to be. It pairs naturally with cost-of-waiting — that tells
you the price of waiting, this tells you *why*.

**Built from:** the projections of available players, sorted. No simulation.

**Effort: small.** Genuinely a few lines plus a display. **Best value for money
in this document.**

## 2.2 Run detector

Compare how fast a position is actually coming off the board against how fast the
simulation expected it to. If five running backs went in the last eight picks and
the model expected two, a run is happening.

**Why it helps.** Runs are the main thing that invalidates a plan mid-draft, and
by the time you notice one by eye you are usually already late. We are uniquely
placed to detect this because we have a live expectation to compare against — a
tool without a simulation can only show you the raw count and let you guess
whether it is unusual.

**Built from:** the pick log for what happened, the simulation for what was
expected.

**Effort: medium.** The comparison is easy; the judgement is in the threshold.
Too sensitive and it cries wolf every round, which is worse than silence.

## 2.3 Opponent need radar

For each team picking between now and your next turn, work out what they still
need — their starting slots minus what they have already drafted. Summarise it:
"5 of the 7 teams before your next pick still need a running back."

**Why it helps.** It turns "will he last?" from a probability into a reason. The
percentage tells you *what* will likely happen; this tells you *why*, and lets
you sanity-check the model against what you can see. It is also the most
human-legible thing in this list — it is exactly how experienced drafters
actually think.

**Built from:** roster counts per team, which the state object already computes,
and the league's starting slots.

**Effort: small.** No simulation, no new data.

## 2.4 Tier survival — "will one of these get back to me?"

Let me pick several players I would be equally happy with, and tell me the chance
that **at least one** of them is still there at my next pick.

**Why it helps.** This is the correct question and almost nobody asks it,
because most tools cannot answer it. Four backs at 30% each is not a 30% problem
and it is not a 76% one either — they compete for the same picks, so their fates
are linked. Treating them as independent *understates* the true chance, sometimes
by nine percentage points. Getting this right is the clearest payoff of keeping
the whole simulation rather than a summary.

**Built from:** `prob_any_available`, which is **already written and tested** and
already wrapped by the board object. It is simply not surfaced in the Runner's
interface.

**Effort: small — the smallest here.** The hard part is done; this is a
multiselect and a number. If you build one thing from this document, build this.

## 2.5 Need-adjusted value board

The console currently ranks players by projection and by value over replacement,
both of which ignore what *you* already have. Add a score that weights value by
your own remaining needs: a fourth running back is worth much less to you than
your first tight end, even if he projects higher.

**Why it helps.** It is the gap between "best player available" and "best pick
for me", which is the actual decision. Right now you do that arithmetic in your
head every pick.

**Built from:** your roster counts, the league's starting slots, and existing
VORP.

**Effort: medium.** The mechanics are simple; the weighting is a judgement call
that needs tuning against real drafts. Show it *beside* raw value rather than
replacing it, so you can see when the two disagree.

## 2.6 Two-pick pair planner

Rather than optimising the next pick alone, look at your next two together: is
"running back now, receiver next" better than the reverse? Simulate both orders
and compare the pair of players you would realistically end up with.

**Why it helps.** Back-to-back picks at the turn are where order matters most,
and it is genuinely hard to reason about — the right first pick depends on what
survives to the second. This is the kind of question a simulation is *for*.

**Built from:** the same forward-simulation machinery as the positional
suggestion above.

**Effort: large.** The most expensive idea here, both to build and to run. Worth
doing only after the single-pick lookahead proves useful.

## 2.7 Falling player and reach alerts

Flag two things: players still available well past their ADP (value falling to
you) and players taken well before it (evidence your leaguemates value a position
more than the market does).

**Why it helps.** The first finds bargains. The second is the more interesting
one — if three receivers have gone 15 picks early, your league is receiver-hungry
and every receiver ADP in your plan is optimistic. That is a live correction to
the model's assumptions, visible nowhere else.

**Built from:** the ADP target already on the table, compared against the actual
pick log.

**Effort: small.**

## 2.8 Structural roster checks — byes, stacks, handcuffs

Three cheap checks that all use fields we already have and never touch:

- **Bye collisions** — flag when a pick would give you three or more starters off
  in the same week.
- **Stacks** — flag when an available receiver plays for the same NFL team as
  your quarterback. Some people want this deliberately.
- **Handcuffs** — flag when an available running back backs up one you already
  own. Insurance, and a common late-round strategy.

**Why it helps.** These are structural mistakes and structural opportunities that
have nothing to do with projections, so no amount of staring at the value column
reveals them. They are exactly what you forget under time pressure.

**Built from:** the bye week and NFL team columns, both already loaded and both
currently unused.

**Effort: small.** All three together are less work than any single simulation
feature.

## 2.9 Live team ranking

After every pick, show where your team ranks in projected starting points, and
how the gap to first place is changing.

**Why it helps.** It converts a long sequence of individual decisions into
feedback you can act on. Watching yourself slide from 4th to 8th over three
rounds is a prompt to change approach while there is still draft left. It also
makes a practice sim genuinely useful for learning, which is the point of having
practice sims.

**Built from:** the same per-team starting totals as the team-strengths panel.

**Effort: small** once that panel exists — it is the same calculation, shown over
time.

**Caveat.** Rankings this early are noisy, and a big early gap mostly reflects
who got the early picks rather than who is drafting well. Show it as a trend, and
resist making it feel like a score.

## 2.10 What-if replay

Rewind to any earlier pick, take a different player, and play the draft forward
to compare the two outcomes side by side.

**Why it helps.** It is how you actually learn to draft better — not from advice
during the draft, but from seeing afterwards what the other branch looked like.
This is also the feature nobody else can offer honestly, because it requires the
opponents to behave *identically* in both branches. Ours do: the simulated
managers' opinions are drawn once from a stored seed, so rewinding and replaying
reproduces their picks exactly. That property is already built and tested.

**Built from:** the existing rewind, the deterministic AI, and the roster scoring
from the positional-suggestion idea.

**Effort: medium.** Mostly interface work — holding two branches at once and
presenting the comparison.

**Note:** this only makes sense in a practice sim, not a live draft, since it
depends on being able to re-run the opponents.

---

# What I would build first

Roughly in order of value per unit of work.

**Start here — small effort, real payoff:**

1. **Tier survival** (2.4) — the machinery is already written and tested. This is
   the single best use of the simulation and it is currently invisible.
2. **Positional cliff finder** (2.1) — answers the most common in-draft question,
   costs almost nothing.
3. **Bye collisions** (part of 1.2 and 2.8) — a real mistake, cheaply caught,
   using data already loaded.
4. **Opponent need radar** (2.3) — turns probabilities into reasons.

**Then the panel:**

5. **Team relative strengths** (1.2) — starting totals, internal replacement, and
   risk/upside together. One coherent panel, and 2.9 falls out of it for free.

**Then the ambitious one:**

6. **Positional suggestion** (1.1) — genuinely valuable, but do it after the
   cheap wins, and be careful about implying precision the projections cannot
   support.

**Leave for later:** the two-pick planner (2.6) and what-if replay (2.10). Both
are good ideas that depend on the single-pick lookahead being proven first.

**One thing to resist.** Every idea here adds something to look at, and the
console is already dense. A tool that shows you nine numbers per player does not
help you decide faster — it helps you hesitate. As these land, be willing to
*remove* things that turned out not to change any decision. The cost-of-waiting
chart replacing a row of metrics was that kind of trade, and it was the right one.

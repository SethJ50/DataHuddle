# Predicting Draft-Position Width (stdev) — Implementation Notes

## The idea

My availability model needs two numbers per player: **center** (ADP) and **width** (stdev of draft position).

- ADP is safe. Every source publishes it.
- Width is fragile. Only some vendors publish it, and FFC could disappear.

**Plan:** while FFC's stdev is available, train a model that predicts stdev from
predictors I'll always have. Save it. If FFC goes away, keep predicting widths.

This is called **surrogate modeling** (or distillation) — using a resource I have
now to teach a model that works after it's gone. Like photographing a document
before returning it.

It replaces my earlier `width = a + b × adp` guess, which was the same idea with
one predictor and a hand-assumed shape. Let the data pick the shape instead.

---

## THE RULE (everything depends on this)

> **Every predictor must survive the disaster I'm insuring against.**

The classic failure: build a beautiful model, feel safe, then discover in a crisis
that three predictors also came from the vendor that vanished. The insurance was
underwritten by the thing it insured.

**Before writing training code, make a table with a column: "where does this come
from if FFC is gone?"** Anything I can't answer gets cut, however predictive.

### Subtle version of the trap

Some of the *best* predictors are other vendors' spread numbers (FantasyPros rank
stdev, FFC-vs-MFL ADP disagreement). Strong — but they're spread measures too, so
they'd plausibly vanish in the same kind of event.

**So build TWO models:**

| Model | Uses | Purpose |
|---|---|---|
| **Rich** | Everything, incl. other vendors' spread columns | Everyday use. Most accurate. |
| **Austere** | Only Tier 1 + Tier 3 (see below) | Actual doomsday insurance |

Train both, evaluate both, save both. Knowing the austere model is (say) 20% worse
is information I want on a calm Tuesday, not during draft week.

---

## DO FIRST — before any modeling

### 1. Pull all historical FFC years, right now

The FFC endpoint takes a `year` param. Historical seasons are sitting there today.
Pull every year available and write to disk.

```
raw/ffc/2026.json, raw/ffc/2025.json, raw/ffc/2024.json, ...
```

Why this beats the model in urgency:

- **Raw data outlives models.** With 5 years of snapshots I can retrain, re-specify,
  and fix bugs forever. With one saved model and no data, I'm stuck with whatever
  choices I made that day, bugs included.
- **One season can't train this anyway.** A single snapshot can't distinguish a real
  relationship from a 2026 quirk, and gives no way to test year-over-year stability —
  which is the whole question.

### 2. Start weekly snapshots now

ADP and stdev both move as news breaks, so each week is genuinely new observations,
not duplicates. Eight weeks of snapshots multiplies training data and captures
within-season dynamics.

Also required for the best Tier 3 feature (ADP volatility) — today's snapshot is the
only way to have 4 weeks of history in 4 weeks.

---

## Modeling choices

### Predict log(stdev), not stdev

Widths grow with ADP — ~3 picks in round 1, ~35 in round 12. Training on raw stdev
means a 5-pick error counts equally at both ends, but that's a rounding error late
and a catastrophe early. The model would optimize for late-round players and be
sloppy exactly where precision matters.

```python
y = np.log(df["stdev"])
# ...train...
predicted_stdev = np.exp(model.predict(X))
```

Logs make the model care about *proportional* error. Also guarantees positive
predictions — a linear model on raw stdev will predict a negative standard deviation
for someone and crash the curve code.

### Keep the model simple — and here that's not just beginner advice

This model gets used **years from now, on drifted data.**

- A **linear model** outside its training range degrades smoothly and predictably —
  it keeps extrapolating the trend it learned.
- **Gradient-boosted trees** can only output values seen in training, so they
  flatline at the edges and behave erratically in unseen regions.

The stated purpose is robustness under unforeseeable conditions → favor a model whose
failure mode I can reason about. Try both, but weight toward simple. **If boosting
only wins by a few percent, take the linear model.**

### Validate by season, NOT randomly

Most important methodological point.

```python
train = df[df.season <= 2024]
test  = df[df.season == 2025]
```

A random split answers "can it predict unseen players?" — not my question. My question
is "can a model trained in the past predict the future?", which IS the deployment
scenario.

- Predicts the held-out year well → real evidence the relationship is stable.
- Degrades badly year over year → needs annual retraining, and has a shelf life I
  should write down.

---

## Candidate predictors

Bold = expect most of the signal.

### Tier 1 — any ADP source anywhere gives these (austere model)

| Predictor | Why |
|---|---|
| **ADP** | Dominant predictor; width scales with it |
| **log(ADP)** | Scaling is multiplicative, not additive |
| **Undrafted rate** | Directly encodes the truncated-tail logic |
| **Position** (one-hot) | QB/TE draft variance differs from RB/WR |
| ADP ÷ total picks | Position in draft, independent of league size |
| Round number | Round-boundary effects |
| Position rank (WR12, RB8) | Within-position scarcity |
| **ADP gap to next same-position player** | Tier-cliff proximity — cliffs get reached for |
| ADP gap to previous same-position player | Other side of same effect |
| Local crowding (players within ±5 picks) | Dense = substitutable = more variance |

### Tier 2 — other vendors (RICH MODEL ONLY)

| Predictor | Why |
|---|---|
| **Cross-source ADP disagreement** (FFC vs MFL vs FantasyPros) | Independent read on the same disagreement |
| **FantasyPros ECR stdev** | Direct measure of expert disagreement |
| Best/worst rank range | Cruder version of above |
| Rookie flag | Genuinely wider outcomes |
| Changed NFL team this offseason | Role uncertainty |
| Injury designation | Volatile, news-driven |
| Age / years experience | Older players polarize drafters |

### Tier 3 — I generate these; fully durable (austere model)

| Predictor | Why |
|---|---|
| **ADP volatility, trailing 4 weeks** | Best Tier 3 feature by a distance — see note |
| ADP velocity (2-week change) | Direction and speed of movement |
| FFB Upside score | Always have it |
| FFB Risk score | Always have it |
| Upside − Risk | Asymmetry (feeds the skew logic) |
| Hype knob | Always have it |

**Note on ADP volatility:** a player whose ADP swung 40 → 55 → 45 over a month is one
the market can't decide on — the *same underlying disagreement* that produces high
cross-draft variance. Likely one of the strongest predictors, needs no vendor's derived
column, and I own it outright.

---

## How to save the model properly

Pickling and calling it done is the beginner trap. Pickles are version-fragile — a
scikit-learn upgrade in 18 months can make it unloadable, discovered at the worst
possible moment.

**Save four things:**

1. **The pickle** — for convenience
2. **Plain coefficients** (JSON/CSV) — for a linear model on log(stdev) this is just
   feature names + numbers. Reimplementable in 5 lines of arithmetic in any language,
   forever. Never breaks.
3. **The training data snapshot** that produced it
4. **A text file**: feature list, target transformation, train/test split, error metrics

#4 is for future-me, who will find a model file with no idea whether it predicts stdev
or log(stdev). That's a real bug that silently produces widths off by a factor of e.

---

## Then run it in shadow mode

Once trained, compute predicted stdev alongside real FFC stdev on every pull. Log both.

Gives a live readout of how the insurance is performing on current data. If the gap
widens in Aug 2027, that's the retrain signal — seen coming, rather than discovered
during a failure.

That's the difference between a backup I believe in and a backup I hope works.

---

## Implementation checklist

- [ ] Pull all available historical years from FFC → `raw/ffc/{year}.json`
- [ ] Set up weekly snapshot job (ADP + stdev + times_drafted)
- [ ] Build the feature-durability table; assign every predictor to Tier 1/2/3
- [ ] Build feature engineering pipeline (Tier 1 + 3 first)
- [ ] Train austere model: linear regression on log(stdev), season-based split
- [ ] Train rich model: add Tier 2 features
- [ ] Try gradient boosting on both; only adopt if it wins by a lot
- [ ] Record error metrics for both; note the accuracy gap
- [ ] Save all four artifacts per model, versioned by training date
- [ ] Wire shadow-mode logging into the daily pull
- [ ] Cross-check predicted widths against the undrafted-rate solver (independent estimate)
- [ ] Run the counting-identity smoke test (`sum of P(gone by k) ≈ k−1`) on predicted widths

---

## Related notes

- The **undrafted-rate solver** (two equations, two unknowns: reported ADP + undrafted
  rate → true center + width) is now an *independent cross-check*, not a replacement.
  Two estimates agreeing is much stronger than one.
- The **counting identity** (`sum of P(gone by pick k) = k − 1`) is a smoke detector,
  not an estimator. It won't finely tune widths but will loudly catch factor-of-two
  errors, sign flips, and unit mixups — especially in round 1, where the surrogate
  model has the least data.
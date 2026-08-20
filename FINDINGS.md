# Project Findings Log

Running record of decisions, results, and definitions for interview prep. Updated as we go — newest phases at the bottom of each section.

## Dataset

**Domain:** PayPal-style digital wallet, one account per user.

**Time period represented:** a simulated **180-day observation window** (`Day_Number` 1–180 in the data). This is not tied to real calendar dates — it represents roughly a 6-month slice of transaction history we're assumed to be looking back over. Each user's transactions are spread across this window; the very first transaction we observe for a given user is *not* assumed to be their account's actual first-ever activity — we sample a realistic "time since unknown prior activity" gap for it (see `Days_Since_Last_Activity`), since our observation window doesn't necessarily start at account creation.

**Size (v4, current):** 51,275 transactions across 4,000 users. Overall fraud rate: ~7.76% (v3 was ~7.5%; the small increase comes from the new device/email risk nudges on `account_takeover`/`stolen_funding_instrument`, described below).

**Device modeling (v3):** each user owns a small set of real devices (`Device_ID`, usually 1–3 per user), each with its own type (Mobile/Desktop/Tablet). `Device_Mismatch` is computed from Device ID (has this exact device ever been seen on this account before), not device type — switching between your own phone and laptop is normal and NOT a fraud signal; using a device never seen on the account before IS.

**Enrichment reference tables (v4, new):** `device_threat_intel.csv` (keyed by `Device_ID`: `Is_Jailbroken`, `Device_Threat_Rating`) and `email_risk_data.csv` (keyed by `Email`: `Email_Age_Days`, `Email_Risk_Rating`) — deliberately kept separate from `paypal_transactions.csv`, representing costly third-party lookups the agent queries selectively rather than free columns. See "Gray-zone design" section below and `METHODOLOGY.md` Step 8 for why these were added and how they were validated.

**Two fraud archetypes** (full detail in `generate_synthetic_data.py`'s docstring):
1. **account_takeover** — device/IP-location mismatch from the user's normal pattern, money drains out via Send Money or Withdraw to Bank funded from PayPal Balance, often to a new recipient or newly-linked destination bank, higher amounts, sometimes odd hours, disproportionately likely on **dormant** accounts (long gap since last activity).
2. **stolen_funding_instrument** — a linked bank/card funding the transaction that was added very recently, "card testing" bursts (several rapid attempts, high decline rate), skewed toward high-resale-value purchase categories (Electronics, Gift Cards, Travel).

Neither archetype is perfectly separable from label noise — by design (a system that scores ~100% would itself be suspicious).

**Label semantics:** `Fraudulent=1` means *confirmed* fraud (via customer dispute investigation or proactive fraud-team confirmation), not merely suspected — applies whether the transaction was Approved or Declined by the issuer (a declined fraud attempt is still a fraud attempt).

**Known, documented simplifications:**
- `Previous_Fraudulent_Transactions` is tracked at the **account level only**. A stronger production signal would track a specific stolen instrument (card/bank account) across the *entire* PayPal ecosystem (i.e. across other users' accounts too) — not modeled here, called out as future work.
- Feature engineering for the baseline model (each user's "home" device/location) uses their **full transaction history**, not strictly prior-in-time-only — a simplification vs. a fully causal production setup. Documented in `train_baseline_model.py` and `features.py`.
- `Number_of_Transactions_Last_24H` and `Decline_Rate_Last_24H` are generated as plausible, archetype-conditioned values per row, not literally aggregated by counting neighboring rows in the file.

## Data quality validation performed

- Cross-tab checks confirmed no logically-impossible combinations (e.g. `Counterparty_ID` is "SELF-BANK" if and only if `Transaction_Type` = Withdraw to Bank; `Funding_Source` rules hold for Receive Money / Withdraw to Bank; `Purchase_Category`, `Funding_Instrument_Age_Days`, `Withdrawal_Destination_Bank_Age_Days` are populated only where they logically apply) — **0 mismatches** on all checks.
- Archetype signal strength (re-verified after each fix): account-takeover rows show ~99.5–99.7% device/IP-location mismatch vs. ~0% on normal rows; stolen-funding-instrument rows show ~57–59% decline rate vs. ~4% normal, and 100% high-risk purchase category.
- Ground-truth contributing-factor lists were checked for internal accuracy (e.g. "dormant_account_reactivated" only listed when the dormancy condition was actually true) — 2 bugs found and fixed here.

## Phase 2 — Baseline predictive model

**Purpose:** produces a fraud risk score that becomes one *tool* (`get_risk_score`) the Decision Agent can call — not the fraud decision itself. The model never sees the true generating archetypes/formula; it only sees the same raw columns the agent will see, and has to find the pattern statistically.

**Data split:** user-level (not row-level) 70% train / 15% validation / 15% test, via `GroupShuffleSplit`, `random_state=42`. User-level (rather than row-level) matters because a user's transactions are correlated — splitting by row would let the model implicitly "see" a user's pattern in training and get an artificially easy time recognizing that same user in the test set. No user appears in more than one split.
- Train (v4): 36,116 rows, 2,800 users (~7.7% fraud)
- Validation (v4): 7,764 rows, 600 users (~7.6% fraud)
- Test (v4): 7,678 rows, 600 users (~8.2% fraud)

(Row/user counts shift slightly each time the generator is re-run after a logic change, since the number of archetype attempts and card-testing bursts depends on the random draws — not a bug, just a byproduct of the generative process.)

Model selection is based on **validation** performance; the **test** set is only evaluated once, at the end, as the final unbiased check — standard practice to avoid quietly overfitting your model-selection decisions to the test set.

**Results (v4, after adding the device/email enrichment tables and their partial fraud-probability correlations):**

| Model | Split | Precision | Recall | F1 | ROC-AUC |
|---|---|---|---|---|---|
| Logistic Regression | train | 0.610 | 0.789 | 0.688 | 0.885 |
| Logistic Regression | validation | 0.606 | 0.807 | 0.692 | 0.882 |
| Logistic Regression | test | 0.631 | 0.802 | 0.706 | 0.887 |
| Random Forest | train | 0.632 | 0.798 | 0.706 | **0.960** |
| Random Forest | validation | 0.638 | 0.822 | 0.719 | 0.896 |
| Random Forest | test | 0.632 | 0.803 | 0.708 | 0.879 |

Baseline for comparison: random guessing at the base rate would give ~8% precision (test set). Both models are dramatically better than that. Results are essentially unchanged from v3 (within normal run-to-run noise) — expected, since `Email`/device-threat data are intentionally NOT part of the risk model's feature set (`features.py`); the gray-zone work in v4 is about giving the *agent* something extra to reason with, not about improving the baseline model itself.

**Notable finding (still holds in v4):** Random Forest's train ROC-AUC (0.960) is much higher than its validation/test ROC-AUC (0.896 / 0.879) — a real overfitting gap. Logistic Regression is far more stable across all three splits (0.885 / 0.882 / 0.887) — barely any train-vs-validation-vs-test gap at all. On validation/test performance the two models remain close to tied on precision, recall, and F1. **Takeaway (unchanged from v3):** no real performance argument for Random Forest's extra complexity — Logistic Regression matches it closely on every operational metric while being far more stable and more interpretable. Kept as the selected model.

**Top features (Logistic Regression, current selected model):** Decline_Rate_Last_24H, Location_Mismatch, Device_Mismatch, Funding_Instrument_Age_Days, Withdrawal_Destination_Bank_Age_Days, Previous_Fraudulent_Transactions — these line up exactly with the signals deliberately engineered into the two archetypes, a good sanity check the model is learning real structure. Decline_Rate_Last_24H remains the single strongest feature.

**Recall by true archetype:** account_takeover ~99%, stolen_funding_instrument 100%, unexplained/friendly fraud 0% (expected — see "Friendly fraud" discussion below).

## Gray-zone design: why device/email enrichment tools were added, and what they actually prove

**The problem, found by actually inspecting the score distribution (not assumed):** the risk model's output is strongly **bimodal** — ~89-90% of transactions score low (0-0.15/0.3 range, ~1.5-1.9% fraud rate within that band), ~10% score high (>0.85, ~60-65% fraud rate within that band), and almost nothing lands literally in between. Within the high-score band, `account_takeover` already had a natural source of ambiguity (the dormancy boost in its fraud probability is partial), but `stolen_funding_instrument` did not — its signals fired as one all-or-nothing bundle, so the risk model alone could not distinguish which SFI-pattern cases were the real fraud attempts vs. the ones that would resolve as legitimate.

**The fix:** two new external reference tables (`device_threat_intel.csv`, `email_risk_data.csv` — see Dataset section above), each wired with a PARTIAL (not deterministic) correlation into the underlying fraud probability of the archetype it maps to: device/jailbroken-status → account_takeover, email-age/risk → stolen_funding_instrument.

**Verified this is a real, non-decorative signal (not just descriptive data sitting unused):** filtered to cases the risk model already scores as high-risk (score > 0.85) — i.e. the model's own "ambiguous middle," since that's genuinely where 60-65% turn out to be fraud and 35-40% don't —

- Among high-score `account_takeover` cases: jailbroken-device cases are confirmed fraud 43.9% vs. 29.4% for non-jailbroken-device cases (a real, if imperfect, 14.5-point split the risk model itself cannot see, since device-threat data isn't in its feature set).
- Among high-score `stolen_funding_instrument` cases: new/high-risk-email cases are confirmed fraud 16.5%-rate-of-new-email vs. 7.9%-rate-of-new-email for the legitimate ones (i.e. new email shows up roughly twice as often among the confirmed-fraud cases as among the legitimate ones in this same high-score band).

This is the concrete evidence behind "why would the agent need to call more tools instead of just trusting the score" — the score genuinely cannot resolve these cases on its own; the enrichment tools partially can. Also deliberately imperfect on purpose: a handful of curated counter-examples exist (a jailbroken device that still turned out legitimate; an old, low-risk email that still turned out to be fraud) — see `DEMO_SET.md` — so the agent has to weigh evidence, not apply a rule.

## Anticipated question: why is the score distribution bimodal, not a smooth range?

Worth having a ready answer for, since it looks unusual at first glance. The model's strongest features are near-categorical, not continuous: a device has either been seen on this account before or it hasn't (no "50% recognized"); a funding instrument was linked 2 days ago or 500+ days ago, essentially nothing in between by construction; a decline rate consistent with card-testing clusters around ~60%, not ~15%. A model dominated by signals shaped like that produces a bimodal score by default — this is expected, not an artifact of synthetic data specifically. Real fraud models leaning on device fingerprinting, blacklist hits, or velocity spikes commonly look this way too; the smooth S-curve intuition mostly applies to models built from many weakly-predictive continuous features (e.g. traditional credit scoring), which this isn't.

More importantly: the genuine ambiguity in this system was never "a score sitting near 0.5." It's that even within the confidently-pattern-matched high band (score > 0.85), only ~60% of matching cases are confirmed fraud — the score says "this strongly resembles a known risky pattern," not "this is fraud." That residual split is exactly what `device_threat_intel`, `email_risk_data`, and `lookup_user_history` exist to help resolve — no amount of score-threshold tuning closes that gap, only additional evidence and reasoning can. Ambiguity here means "confidently pattern-matched but not fully resolved," not "the model doesn't know" — a more precise framing than the smooth-curve intuition would have given us anyway.

## Phase 3 — tiered routing (auto-approve vs. agent review)

**Decision, grounded in the score-band data above, not assumed:** two paths, not three. Scores from 0 up to 0.85 are statistically flat (~1.8% fraud the whole way — no real distinction worth splitting into "low" vs. "medium"), and even the high band above 0.85 only reaches ~60% precision — not confident enough to auto-reject on score alone. So there's no clean "certain fraud, skip the agent" tier in this data; every transaction that reaches the 0.85 threshold, or trips the `policy_lookup` mandatory rule (regardless of score), gets full agent review. Everything else is auto-approved with zero LLM calls.

**Verified on the full dataset:** 45,671 transactions (89.1%) auto-approved, 5,604 (10.9%) routed to agent review. Fraud rate in the agent-review band: 57.0% — genuinely ambiguous, as intended. Fraud rate in the auto-approved band: 1.72% (786 rows) — this is the explicit, accepted cost of the tiered architecture: a small amount of fraud (almost entirely the unlearnable "friendly fraud" category) never reaches any review at all, in exchange for not spending an LLM call on the ~89% of transactions where it wouldn't change the outcome anyway. Worth being able to name directly if asked "what's the failure mode of this architecture" — it's not hidden, it's a deliberate, quantified tradeoff.

Of the agent-review band: 4,564 routed on score alone, 564 on policy alone, 476 on both.

## Friendly fraud (formerly "unexplained/noise fraud")

About 30% of confirmed fraud rows (`True_Archetype = "none"`, `Fraudulent = 1`) match neither archetype, by design — this models **friendly fraud**: a customer disputes a transaction that was, at the time it happened, completely normal-looking (no device, location, velocity, or funding-instrument anomaly to find). It's confirmed fraud (so it counts in the fraud rate and the recall denominator), but there is zero transaction-time signal to learn from, on purpose. This is why overall recall is capped well below 100% by construction, not a model weakness — see the archetype-level recall breakdown above and the "would separate per-archetype models help" investigation below, both of which confirm the shortfall is fully explained by this one category. It also motivates a specific behavior for the Decision Agent: when it finds no red flags, it should say so plainly rather than implying the transaction is provably clean — "no transaction-time signal found" is a different (and more honest) claim than "this is definitely legitimate."

## Metric definitions (plain language, for your own reference)

- **Precision:** of everything the model flagged as fraud, what fraction actually was fraud. Low precision = lots of false alarms (annoying legitimate customers).
- **Recall:** of all the real fraud that existed, what fraction did the model catch. Low recall = fraud slipping through undetected.
- **F1 score:** a single number balancing precision and recall (their harmonic mean) — useful when you care about both and don't want to optimize one at the total expense of the other.
- **ROC-AUC:** measures how well the model *ranks* fraud above non-fraud across every possible decision threshold, not just the default 0.5 cutoff. 0.5 = no better than random guessing; 1.0 = perfect separation. Less sensitive to your choice of threshold than precision/recall/F1 are.
- **FNR (False Negative Rate)** *(noted since you're studying this — not yet computed/reported above, can add on request)*: the fraction of actual fraud cases the model *missed* — i.e. `1 - recall`. In fraud detection this is often the metric that matters most to the business, since a missed fraud case (false negative) is usually more costly than a false alarm (false positive).

## Investigated: would separate per-archetype models outperform one shared model?

Checked empirically rather than assumed (originally on v3 data; re-spot-checked after v4, conclusion unchanged). The single shared model catches ~99% of account-takeover fraud and 100% of stolen-funding-instrument fraud — both archetypes essentially perfectly — and nearly every transaction it flags traces back to one of these two real archetypes. No imbalance between archetypes for separate models to fix. The model's overall recall shortfall is fully explained by its 0% recall on "none" (friendly-fraud) rows — which is expected and correct, since that ~30% of fraud is deliberately unexplained/unlearnable by design. **Conclusion: separate per-archetype models would not improve performance here** — a single model already specializes internally via different feature patterns per archetype. Not pursued further.

## Decision: switched from Random Forest to Logistic Regression (final)

Updated decision, replacing the earlier "keep Random Forest" call: we tried both models, and on validation AND test they are statistically tied on every operational metric — precision, recall, and F1 are identical or within a rounding error. Random Forest showed a real overfitting gap (train ROC-AUC 0.963 vs. validation/test 0.883/0.874); Logistic Regression did not (0.887/0.885/0.883 — essentially flat). Given no genuine performance advantage, we deliberately chose the simpler, more stable, more interpretable model rather than defaulting to whichever won by a hair on one metric. Verified this doesn't cost us anything at the archetype level either: Logistic Regression catches 98.4% of account-takeover fraud and 100% of stolen-funding-instrument fraud on the test set — identical to Random Forest.

`risk_model.pkl` now contains the Logistic Regression model (`train_baseline_model.py` selects it explicitly and documents why, rather than picking automatically by validation F1).

Further modeling work (XGBoost/LightGBM, systematic hyperparameter tuning, calibration, deeper threshold optimization against real business costs, etc.) remains consciously deferred — tracked in **`OPEN_ITEMS_FOR_LATER.md`**.

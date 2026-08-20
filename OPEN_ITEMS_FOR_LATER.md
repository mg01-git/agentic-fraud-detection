# Open Items / Ideas for Later (Extra-Credit Pass)

Things deliberately deferred to keep today's build moving, tracked here so nothing gets lost. Not urgent — revisit if there's time at the end, or if asked about in an interview ("did you consider X?" — yes, here's why we deprioritized it, not that we didn't think of it).

## Modeling improvements (Phase 2 — baseline risk model)

| Approach | Status | Outcome / reasoning |
|---|---|---|
| Logistic Regression | **Tried — SELECTED (final)** | Train/val/test F1 (v4): 0.688 / 0.692 / 0.706. Close to Random Forest on every operational metric; far more stable (no overfitting gap: ROC-AUC 0.885/0.882/0.887). Deliberately chosen over Random Forest for this reason — see FINDINGS.md. |
| Random Forest (300 trees, depth 8) | **Tried — not selected** | Train/val/test F1 (v4): 0.706 / 0.719 / 0.708 — close to Logistic Regression, but shows a real overfitting gap (train ROC-AUC 0.960 vs. validation/test 0.896/0.879). |
| Threshold tuning (vs. default 0.5) | **Tried** (quick pass) | Tested 0.2–0.8 on validation. 0.4–0.6 all give identical results (452 flagged, precision 0.659, recall 0.732) — 0.5 sits in a flat, stable region, so not a bad default here. Extremes are much worse (0.2 → 6% precision at 96% recall; unusable volume of false alarms). Not deeply optimized against an actual business cost function (cost of a false positive vs. a false negative) since we don't have real dollar costs to optimize against — could revisit if we define hypothetical costs. |
| XGBoost / LightGBM | **Not tried** | Neither installed in this environment. Gradient-boosted trees often outperform Random Forest on tabular data like this. Worth trying if we want to push validation F1/ROC-AUC higher — moderate effort (install + adapt training script, already have `features.py` shared module ready to reuse). |
| Systematic hyperparameter tuning (grid/random search + cross-validation) | **Not tried** | Random Forest's depth/tree-count and Logistic Regression's regularization strength were set to reasonable defaults, not tuned. Likely modest additional gains available. |
| Probability calibration (Platt scaling / isotonic regression) | **Not tried** | Matters most if the Decision Agent reasons using the raw score value (e.g. "62% risk") rather than just a flag/no-flag threshold — worth checking once the agent's prompt is written, to see if calibration actually matters for how the score gets used/explained. |
| Class-imbalance handling beyond `class_weight="balanced"` (e.g. SMOTE) | **Not tried** | Often smaller real-world gains than expected, and adds complexity/leakage risk if not done carefully within the CV folds. Low priority. |
| More/richer feature engineering (e.g. explicit interaction terms, causal-only "prior activity" features instead of full-history) | **Not tried** | Highest potential payoff of anything on this list, also highest effort. The full-history vs. prior-only leakage simplification (noted in FINDINGS.md) lives here too. |

## Device fingerprinting granularity — RESOLVED (v3)

~~`Device_Used` only has 3 values...~~ Fixed: added `Device_ID` (unique per physical device, each user owns a small known set of 1-3), with `Device_Mismatch` now based on device ID history, not type/category. See METHODOLOGY.md Step 6 and FINDINGS.md for updated results. Still a simplification vs. real production device fingerprinting (browser fingerprint, hardware/OS signature, etc.) but meaningfully closer to realistic than the original category-only version.

## UI — deliberately tabled

A lightweight case-review UI was discussed and explicitly deferred: not a live app, just something to revisit if there's time at the very end. Mansi's specific vision, worth preserving: a case-search screen showing key transaction info as tiles, with the recommended decision, confidence score, and explanation factors laid out clearly. Decided against building this now because a well-formatted trace/case-file output (Phase 5 tracing, already planned) gives the same demo value — a walkable, screen-shareable record of the agent's reasoning — without the added cost of building and wiring an actual frontend/backend app, which isn't worth it given the time already spent on design. Revisit only if the core agent + eval loop is done with time to spare.

## Agent calibration — worth checking once evals exist

- **Confidence calibration.** The Decision Agent's `confidence` output field is self-assessed by the LLM, not computed from a formula. Once the eval harness (Phase 6) exists, worth checking whether stated confidence is actually well-calibrated (is it more often right when it says "high confidence"?) — a good eval dimension, not a now-decision.

## Dataset gap found during real-agent review — deliberately NOT fixed yet

Found while reviewing T43817's real case file: `account_open_days_ago` (30–2000 days) and each user's transaction count within our 180-day window are drawn completely independently in `generate_synthetic_data.py` — nothing ties them together. This let the generator produce a 1,709-day-old account whose only transaction in the entire dataset is the one being evaluated, which reads as an odd/manufactured combination once you actually look at it (a genuinely long-lived account having exactly one transaction ever, landing on something that also looks like account takeover, strains belief).

**Proposed fix (not applied):** a `min_transactions_for_age(account_age_days)` floor when assigning each user's transaction count — e.g., under 90 days old → minimum 1 (fine, genuinely new), 90–365 days → minimum ~2-3, over a year → minimum ~5-6 — layered on top of the existing random variation, not replacing it (so naturally low-activity long-time users can still occur, just not as the *only* possible outcome for an old account).

**Why deferred:** regenerating means retraining the model, re-verifying the score distribution and routing thresholds, likely new `DEMO_SET.md` transaction IDs, and another documentation pass across FINDINGS/METHODOLOGY/SCHEMA. Real cost, not free — worth doing once, deliberately, not mid-review. Addressed for now at the prompt/tool-semantics level instead (see decision_agent.py: thin-history handling) so the reasoning bug is fixed even though the underlying data imbalance isn't yet.

**Best-case follow-on (Mansi, 2026-08-18):** if there's time for a real regeneration pass, also address the score distribution's bimodality — currently ~89% of transactions cluster in a low-risk band and ~10% in a high-risk band with almost nothing genuinely in between (near-categorical features like device-known y/n and funding-instrument-age cliffs drive this). A regenerated dataset should aim for a real gray zone in the middle of the score distribution, not just two clusters — so that "high score" and "confident decision" aren't nearly synonymous the way they currently trend. Bundle this into the same regeneration pass as the age/window fix above rather than doing it separately.

## Story-based demo curation — established as the standing method, more to build

Confirmed working approach (2026-08-18, see `CASE_REVIEW_FEEDBACK.md` and case files T64/T9253/T26566): rather than regenerating the raw dataset to manufacture "cleaner" examples, curate real rows that already fit a specific narrative ("dormant account takeover," "stolen funding instrument right after linking," etc.), run them through the real agent, and frame each case file around: why it got routed to the agent, what the agent's tool calls specifically added beyond the raw score, and how it resolved. This is now the standing method for building out `DEMO_SET.md`, not a one-off — **more story-based cases still need to be curated** once the funnel (Judge Agent, eval harness) is built and we're back to finishing the demo set. Mansi will supply story prompts one at a time; each gets matched against the real data, not invented.

## Enrichment signals — scope decisions

- **IP/network reputation as a third enrichment axis — explicitly declined for now.** When designing `device_threat_intel.csv` (→ account_takeover) and `email_risk_data.csv` (→ stolen_funding_instrument), a third parallel table (IP/network reputation — e.g. known-proxy/VPN/datacenter flags on `IP_Location`) was considered as a candidate enrichment source. Explicitly deprioritized to keep scope contained to a clean one-to-one mapping (one enrichment tool per archetype) rather than diluting the demo across three signals. Worth naming if asked "what else would you add" — a real production system would likely want this too, especially for account_takeover (IP reputation is often a stronger real-time signal than device history alone).

## Data split methodology

- **Temporal split, not just user-level split.** Our current train/validation/test split is by *user identity* only — all three sets are drawn from the same simulated 180-day window. A more realistic production setup would ALSO split by *time* (e.g. train on days 1–126, validate on 127–153, test on 154–180), since fraud patterns evolve over time and a purely random/user-based split implicitly (and optimistically) assumes the future looks statistically identical to the past. Worth being able to name this limitation if asked; not implemented due to time.

## Other deferred items (from earlier phases, cross-referenced from FINDINGS.md)

- **Instrument-level fraud history across the whole PayPal ecosystem** (not just this one account) — noted back during archetype design as a stronger production signal than our current account-level-only `Previous_Fraudulent_Transactions`. Would require modeling a shared instrument identity across users — a real structural addition, not a quick tweak.
- **22-of-4,000-user edge case** in `Days_Since_Last_Activity` from the original v2 generation pass — this was actually found and fixed (see FINDINGS.md / chat history), noting here just so it's not confused with something still outstanding.
- **FNR (False Negative Rate) and FPR** — not yet added to the results table; quick to compute (`FNR = 1 - recall`), worth adding once you're comfortable with the definitions, since FNR is often the metric that matters most to a fraud team.
- **Multi-model / prompt-engineering comparison** (Claude vs. GPT, prompt iteration with before/after eval scores) — explicitly deferred earlier in the project to a later phase, once the core single-model pipeline works end to end. Tomorrow candidate.

## How to use this file

When we wrap up the core build (Phases 3–6), come back here first before calling the project "done" — pick whichever of these would most strengthen the interview story given remaining time, rather than trying all of them.

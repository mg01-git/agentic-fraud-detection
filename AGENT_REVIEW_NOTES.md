# Real-Agent Review Notes (Live Session)

Running log of everything found while reviewing real Decision Agent output case-by-case against `DEMO_SET.md` transactions. Purpose: collect issues as they're found, then decide together what to actually fix, rather than context-switching on each one mid-review.

## Fixed already (during this same review pass)

1. **Thin-history device/location signal treated as suspicious instead of uninformative.** For a zero-history account, "unrecognized device" is meaningless (every device looks unrecognized) — was being cited as a risk factor anyway. Fixed via explicit prompt instruction (see `decision_agent.py` system prompt).
2. **Explanation was one dense paragraph, no structure.** Fixed: added a `mitigating_factors` field alongside `risk_factors`, required a short (2-4 sentence) explanation that names the specific tension driving the decision, rather than just restating evidence.
3. **Malformed tool-call output** — `risk_factors`/`mitigating_factors` occasionally came back as a string containing literal `<parameter name="...">[...]` text instead of a real JSON array (reproduced twice in a row). Fixed with a repair step (`_repair_list_field` in `decision_agent.py`) rather than assuming this can't happen.
7. **Two more malformed-output shapes found during curated-case review, now fixed:**
   - `<item>...</item>`-wrapped, non-JSON factor lists (seen on T26566's mitigating_factors) — `_repair_list_field` now also extracts `<item>` tag contents directly, not just bracketed JSON.
   - Factors that are technically a valid JSON array but contain garbage content instead of real explanations (seen on T26566's risk_factors, which came back as `["3", "8", "12", "16"]` — literal `hours_seen_before` values grabbed by mistake). Added `_is_valid_factor_list()` — rejects lists where entries are too short or purely numeric.
   - Both are treated as model non-determinism, not a deterministic parsing bug: `run_decision_agent` now retries the **entire** agent run (fresh generation, up to `MAX_DECISION_RETRIES=2`) whenever validation fails, rather than trying to salvage bad content. Verified working live: T26566's first regeneration attempt hit `max_tokens` mid-generation (a real, if rare, separate failure) and was automatically retried and recovered on attempt 2 with a clean, well-formed decision.

## Logged, NOT yet fixed (decide together once review pass is done)

4. **Account age vs. 180-day observation window are decoupled in the dataset.** A very old account (e.g. 1,709 days) can be assigned zero or almost no transactions in our window purely by chance, making "thin history" look like "new account" when it isn't. Proposed fix: age-scaled minimum transaction count in `generate_synthetic_data.py`. Deferred — requires regenerating + retraining + re-validating everything downstream. (Logged in `OPEN_ITEMS_FOR_LATER.md` already.)
5. **Generated 24h velocity/decline fields vs. live-counted fields disagree, and the agent doesn't flag this.** `Number_of_Transactions_Last_24H`/`Decline_Rate_Last_24H` in the raw record are archetype-flavor generated values, not real counts (documented limitation). `lookup_user_history`'s live fields ARE real counts. Seen twice now (T43817: generated said 6 txns/14% decline, real was 0/0; T33510: generated said 11 txns/63% decline, real was 2/50%). The agent currently cites the raw generated numbers in its reasoning without acknowledging the live tool shows something smaller/different. Proposed fix: instruct the agent to treat the live-counted numbers as authoritative when the two disagree, and to name the discrepancy rather than silently picking one.
6. **Redundant `get_risk_score` call.** `routing.py` already computes the risk score to decide this transaction needs agent review at all — the agent then calls the same deterministic tool again as its first move and gets back the identical number. Proposed fix: pass the already-known score into the agent's system prompt/context directly (the transaction arrived already knowing why it was flagged), removing the tool call entirely rather than having the agent "discover" something the system already told it.

## Investigated: "why do very high risk scores still read as ambiguous?"

Mansi's concern: several reviewed cases had near-maximal risk scores (0.96-0.99) but still came back ESCALATE rather than a confident REJECT, which felt like a dataset quality problem (mental model: extreme scores should be the unambiguous ones, only the middle band should be genuinely uncertain).

**Investigation:** rather than regenerating data, searched the existing 51,275-row dataset for the best-fitting "textbook" examples of each archetype already sitting there, then ran them through the real agent to see if the ambiguity was a data-availability problem or something else.

- **stolen_funding_instrument:** found 8 strong candidates (score 0.91-0.97, funding instrument 0.3-2.2 days old, decline rate 0.5-0.7, email age 1-2 days). Ran two through the real agent:
  - **T9253** → REJECT, 85% confidence, clean case file, no ambiguity at all.
  - **T26566** → ESCALATE, 62% confidence — but this is a *legitimate* escalate, not a data problem: the device/location are independently confirmed as the real owner's own (76 days of genuine history), which genuinely conflicts with a brand-new funding instrument and email. That's real competing evidence, not noise.
- **account_takeover:** found 287 strong candidates; picked **T64** (score 0.96, 18 real prior transactions as baseline, device independently confirmed jailbroken + High threat, brand-new withdrawal destination) → REJECT, 85% confidence, clean.

**Conclusion:** the dataset does NOT need regeneration. Confident high-score decisions already exist and are producible today — **curation was the right instinct**. The previously-used demo cases (T43817, T33510) just happened to be closer-call examples than necessary. Swapped in T9253 and T64 as cleaner, more confident picks; kept one genuinely-mixed-evidence escalate (T26566) deliberately, since a fraud agent that can distinguish "strong signal, reject with confidence" from "genuinely conflicting signal, escalate and say why" is a *better* demo story than one that only ever rejects at high scores — it shows the agent isn't just pattern-matching the score, it's weighing evidence.

## Not yet checked

- Whether the agent ever produces "approve" given real evidence (all cases reviewed so far have landed on escalate or reject).
- Case 3 (T8267) and case 4 (T46294) — the two "looked risky, actually legitimate" gray-zone cases — still to review with the current (fixed) prompt.

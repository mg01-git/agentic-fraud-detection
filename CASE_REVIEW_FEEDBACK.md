# Case Review Feedback Log

Running log of Mansi's feedback while reviewing individual case files, captured as raised — **not acted on yet**. Per the "complete the funnel first" plan (see `AGENT_REVIEW_NOTES.md`), nothing here gets fixed until the full funnel (Judge Agent + eval harness) is built and we do one deliberate fix-everything pass at the end. This file exists so nothing said during review gets lost in the meantime.

Each entry is tagged with the case file it came from, in case the same point resurfaces on a later case (worth checking for patterns before fixing).

---

## T64 (account_takeover, REJECT 85%)

1. **Redundant `get_risk_score` tool call.** Case was routed to the agent BECAUSE of the score (`score_only`) — the agent then calls the same deterministic tool again as its first move and gets back the identical number it was already routed on. (Same issue already logged as AGENT_REVIEW_NOTES.md item 6 — this case reconfirms it.)

2. **"Prior confirmed fraud: 1" has no context.** Displayed as a bare count with no detail on when it happened or what kind of fraud it was — leaves an open question for the reader rather than informing them. Consider either dropping this field from the display, or attaching minimal context (date / archetype) if we have it in the data.

3. **Velocity (24h) and decline rate (24h) don't reconcile into a whole number, and force the reader to do math.** "Velocity: 6, decline rate: 0.08" — reading this requires multiplying to figure out how many of the 6 were actually declined (0.48, which itself isn't a whole number — a separate data-generation oddity worth checking). Proposed fix: display **number of declined transactions** (a count) instead of a rate, so it reads directly without mental math.

4. **`lookup_user_history` tool call output is too dense/complicated to read as displayed.** Raw dict dump in the case file is not reviewer-friendly. Needs a more readable presentation (labeled fields, not a raw Python dict repr) — same treatment the top-level transaction record already gets.

5. **Open question: what was the account's actual balance at the time?** The withdrawal amount ($35.90) is being read as a mitigating signal ("small amount"), but we don't display whether that was the entire available balance or just a small draw from a much larger one — changes how "small amount = less risky" should actually be interpreted. Worth checking if we have this data, or documenting that we don't.

**Confirmed narrative read (for the record, this part landed correctly):** ~1-year-old account goes dormant, then someone adds a new bank account and attempts a withdrawal — small dollar amount, but every other signal (unrecognized+jailbroken device, brand-new destination, unfamiliar hour/location, prior fraud flag) points the same way. Story reads clearly despite the display issues above.

---

## Cases still to review

- T9253 (stolen_funding_instrument, REJECT 85%)
- T26566 (stolen_funding_instrument, ESCALATE 62% — genuinely mixed evidence)

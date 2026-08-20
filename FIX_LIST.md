# Master Fix List

One scannable list, pulled together from `AGENT_REVIEW_NOTES.md`, `CASE_REVIEW_FEEDBACK.md`, and `OPEN_ITEMS_FOR_LATER.md` (those files still have the full detail/reasoning behind each item — this is the index, not a replacement). Nothing here has been changed yet. Grouped by what kind of fix each one actually needs, since that's what determines cost/sequencing.

---

## A. Dataset-level (needs regeneration + retraining + re-verification)

1. **Score distribution is bimodal — no real gray zone.** ~89% of transactions cluster low-risk, ~10% high-risk, almost nothing genuinely in between. Near-categorical features (device known y/n, funding-instrument-age cliff) drive this. *(OPEN_ITEMS_FOR_LATER.md — "best-case follow-on")*
2. **Account age vs. 180-day observation window are decoupled.** A very old account can be assigned near-zero transactions in-window purely by chance, making "thin history" look like "new account." *(AGENT_REVIEW_NOTES.md #4, OPEN_ITEMS_FOR_LATER.md)*
3. **Generated 24h velocity/decline fields vs. live-counted fields disagree**, and for `stolen_funding_instrument` specifically, the generated value feeds the fraud-probability formula and ground-truth factor labeling directly — not just a display mismatch. *(AGENT_REVIEW_NOTES.md #5)*
4. **Velocity/decline rate don't reconcile to a whole number** (e.g. "6 transactions, 8% decline rate" — 8% of 6 isn't a whole number), forcing the reader to do math that doesn't even resolve cleanly. Related to #3. *(CASE_REVIEW_FEEDBACK.md, T64)*
5. **No visibility into account balance at time of transaction.** Can't currently tell whether a withdrawal amount was the full available balance or a small draw from a larger one — changes how "small amount" should read as a mitigant. Open question whether this is addable at all, or a documented limitation. *(CASE_REVIEW_FEEDBACK.md, T64)*

## B. Prompt-level (no regeneration needed — edit `decision_agent.py` only)

6. **No logic/instruction for the confidence score.** The `submit_decision` schema currently has zero description on the `confidence` field — the model has no anchor for what the number should mean (probability of being correct? strength of evidence?) or how to calibrate it. Likely cause of the T43625 finding: 80-85% stated confidence on a case that was genuinely only 63% likely to be fraud per the dataset's own ground truth. **(Added today — Mansi's ask.)**
7. **Redundant `get_risk_score` tool call.** `routing.py` already computes the score that got this case routed to the agent; the agent then calls the identical deterministic tool again as its first move. *(AGENT_REVIEW_NOTES.md #6, reconfirmed on T64)*
8. **`</explanation>`-style stray tag leaking into an explanation string.** Seen once (T6007, from the eval harness run) — same malformed-output family as the already-fixed `<item>` tag issue, different field. Not yet reproduced/fixed.

## C. Display / formatting (case file presentation only, `case_file_formatter.py`)

9. **"Prior confirmed fraud: 1" shown with no context** — no date or detail, reads as an open question rather than useful evidence. Consider dropping or enriching. *(CASE_REVIEW_FEEDBACK.md, T64)*
10. **Raw `lookup_user_history` tool output is a dense, unreadable Python dict dump** in the case file — every other section is labeled/formatted, this one isn't. *(CASE_REVIEW_FEEDBACK.md, T64)*

## D. Ongoing work (not a "bug," just unfinished)

11. **More story-based demo cases still need curating** — T64/T9253/T26566 established the method (curate real rows that fit a specific narrative rather than regenerating data); more stories to build once this fix pass is done. *(OPEN_ITEMS_FOR_LATER.md)*

---

## Not on this list (deliberately)

- Cost quantification — explicitly deferred by Mansi until the dataset/score distribution is finalized, since regenerating would change the numbers.
- Judge Agent, full eval harness run — paused (per Mansi's decision) until this fix pass is done, so eval numbers only get computed once, against the final data.

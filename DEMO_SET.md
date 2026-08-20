# Demo Set

Rebuilt from scratch (previous version's Transaction_IDs are stale after the
full dataset regeneration/scoring pivot this session). Curated story-first,
per Mansi's process: she describes a story, we find/confirm a matching real
case from the dataset, and only afterward consider whether the story reveals
a pattern worth changing at the points/routing level (a deliberately separate,
later decision -- not made reflexively per-case).

**IMPORTANT PROCESS NOTE (learned the hard way this session -- TWICE):** any
edit to `generate_synthetic_data.py` -- even one that only changes behavior
for a narrow slice of rows (e.g. the Travel-shipping fix, or the later
Gift-Cards-shipping fix) -- reshuffles the deterministic random-number
sequence for every transaction generated AFTER that point in the script,
which reassigns which story lands on which Transaction_ID. This has now
happened twice: first T1754/T10368/T49866 silently became unrelated
transactions after the Travel/shipping fix (recovered as T24240/T2734/T15244);
then, after the follow-up Gift-Cards-shipping fix, THOSE re-picked IDs went
stale too (T24240 flipped from confirmed `account_takeover` fraud to a
legitimate low-probability transaction; T2734 flipped from a legitimate
trusted-device story to actual `stolen_funding_instrument` fraud). Recovered
again below as T26574/T51839/T30146. **Do not treat a Transaction_ID as
stable across ANY generator edit** -- re-verify (or re-pick) every case
referenced here after touching `generate_synthetic_data.py`, before sending
anything referencing a specific ID. Now that demo case selection is
considered final for this round, further generator edits should be avoided
unless a full re-verification pass is budgeted immediately afterward.

---

## Story 1: "Obvious ATO" -- Mansi's case for a genuine auto-reject tier

**Case:** T26574 (re-picked a second time, after the Gift-Cards-shipping
regeneration invalidated the previous T24240)

**The story:** A long-tenured account (1,640 days old) goes dormant for 82
days. It reactivates with a same-day password change, then sends $1,077.68
(~6.5x this account's typical amount) to a brand-new recipient at midnight,
from an unrecognized device and an unrecognized location (Miami vs. the
account's trusted Austin).

**Ground truth:** Confirmed fraud, `account_takeover`, `True_Fraud_Probability=0.8186`.

**Current system behavior:** Routed to the agent (raw-evidence risk tier
`high`, 6 points: large % of balance moved, dormant reactivation, unusual
hour, password change after dormancy, new recipient). Agent calls
`lookup_user_history` and `device_intel_lookup`, confirms the device is
unrecognized AND jailbroken with a High third-party threat rating, and
**rejects at 87% confidence**.

**Mansi's point -- STILL the open question, not resolved by this outcome:**
auto-reject (routing-level, skips the agent, zero LLM cost) is different
from the agent independently outputting "reject" (still costs a real LLM
call + 2 tool calls). She considers this evidence pattern (unusual hour,
unrecognized device, new recipient, large amount, dormancy reactivation)
thick enough that it shouldn't need agent decisioning at all. Whether to
build an actual routing-level auto-reject tier remains an open, deliberately
deferred decision -- being revisited only after the rest of the demo
stories are collected, to see if a consistent pattern for where to draw
that line emerges.

---

## Story 2: Less-obvious ATO -- genuinely needs the agent, resolves to APPROVE

The category: the raw evidence pattern looks ATO-shaped (old account,
dormancy, password change, then a large money movement) -- same surface
signals as Story 1 -- but here the TOOLS specifically resolve the ambiguity
toward legitimate, which is the actual value the agent adds over a
raw-evidence-only score.

### Variation A: trusted device resolves a balance-draining withdrawal

**Case:** T51839 (re-picked a second time, after the Gift-Cards-shipping
regeneration invalidated the previous T2734)

**The story:** An established account (1,219 days old), dormant for 62
days then reactivated with a same-day password change, withdraws ~32% of
its balance ($334.90 of $707.96) to its own long-established bank account
(370 days old). Raw-evidence tier: `high` (4 points).

**Ground truth:** Legitimate, `Fraudulent=0`, `True_Archetype=none`.

**Agent behavior:** Calls only `lookup_user_history` -- confirms the
device and location are both on the account's true trusted baseline, and
the destination bank is well-established, not new. **Approves at 72%
confidence**, single tool call, correctly judged `device_intel_lookup` as
unnecessary once the device came back known (per the sequencing guidance
added this session).

**Bonus finding, worth keeping in your back pocket:** T12994 -- same
story shape but 85% of balance moved (vs. ~32% here) -- did NOT resolve to
approve even with a trusted device; the agent escalated at 55% confidence,
explicitly reasoning about session-hijack risk ("a compromised-credential
attacker could still be operating from the owner's known device/location").
This is a genuinely useful contrast case if you want to show that device
trust isn't an automatic override -- the agent's confidence in "genuine
owner" erodes as the amount drained gets more extreme, even holding the
device/location signal constant. (T12994 has not itself been affected by
either generator edit, since it was never re-generated -- its Transaction_ID
and story remain as originally verified.)

### Variation B: trusted shipping address resolves a high-risk-category purchase

**Case:** T30146 (re-picked a second time, after the Gift-Cards-shipping
regeneration invalidated the previous T15244)

**The story:** Same dormancy/password-change base pattern, but a $157.79
Electronics purchase (a high-risk, genuinely-physically-shipped category)
instead of a bank withdrawal, at an unusual hour (4 AM). Raw-evidence tier:
`medium` (3 points).

**Ground truth:** Legitimate, `Fraudulent=0`, `True_Archetype=none`.

**Agent behavior:** Calls `lookup_user_history` (confirms device and
location both match the trusted baseline) AND `address_distance_lookup`
(confirms billing/shipping are both Austin -- `same_city`, no mismatch).
**Approves at 78% confidence.** This is a good showcase of the *updated*
tool-calling guidance: even though billing and shipping are visibly
identical on the raw record, the agent still calls the distance tool for a
real confirmation rather than just eyeballing the two city names -- exactly
the behavior change requested this session (previously the agent skipped
the tool when the answer looked "obvious," which was intentionally removed
as a shortcut).

**Status:** All three stories re-verified against the current dataset
(post Gift-Cards-shipping regeneration) and confirmed working end-to-end
via the real Anthropic-backed agent. Note: `Purchase_Category` in
`{"Travel", "Gift Cards"}` no longer produces a `Shipping_Location` at all
(tickets/confirmations go to email, gift cards are bought as instant
digital codes) -- Electronics is now the only genuinely-physically-shipped
high-risk category, so Variation B's candidate pool was drawn from
Electronics specifically.

Next: Mansi's reject and escalate stories within this same "genuinely
needs the agent" category.

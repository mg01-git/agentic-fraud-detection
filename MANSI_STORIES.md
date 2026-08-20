# Mansi's Demo Stories -- Verbatim Log

This file exists specifically so Mansi's own words about what each story is
supposed to represent never get lost or drifted from as we search for /
curate matching cases. Each entry has: the story as she dictated it
(verbatim, only lightly cleaned of transcription artifacts -- filler words,
false starts -- while preserving her exact meaning and phrasing), the
scenario/bucket it represents, and the current match status. **Do not
silently "improve" or reinterpret the story text below when revisiting this
file** -- if a curated/found case doesn't actually match, that's a real gap,
not something to paper over by editing the story to fit the data.

---

## Story 1 -- Obvious ATO, should be auto-rejected (no agent needed)

> "Let's first talk about an ATO case that is obvious and should be auto
> rejected. In my mind, this particular example falls in that category,
> because of the unusual hour, new device, new bank, and the big amount,
> and then certain activation after ninety seven days of dormancy. To me,
> this is an auto reject story because of an ATO."

**Bucket:** account_takeover x auto-reject (routing-level, skips agent)

**Match status:** Matched -- **T26574**. Open design question (not resolved
by this case): whether a real routing-level auto-reject tier should exist
at all, distinct from the agent independently outputting "reject."

---

## Story 2A -- Less-obvious ATO, agent resolves via trusted device

> "Here is a likely ATO case routed to the agent, and the agent rejects it
> using the two tools at its disposal... let's say this account is an old
> account more than two years old with a period of dormancy, let's say
> sixty days or a hundred days of dormancy, changes the password, comes
> in, and let's say transfers all the PayPal balance into their bank
> account. Comes to the agent. The agent pulls up the tool history,
> recognizes this as a trusted device, and concludes that it can be
> accepted."

**Bucket:** account_takeover x agent-review x APPROVE (device trust resolves
ambiguity)

**Match status:** Matched -- **T51839**. Note: Mansi separately flagged that
this case is a *weaker* demo of "agent adds value" than intended, because
the destination-bank-age signal is arguably strong enough on its own to
have settled it without agent discretion -- logged as a raw-evidence-score
change candidate, not a re-match.

---

## Story 2B -- Less-obvious ATO, agent resolves via trusted shipping address

> "Another alteration of this could be is that this account holder, instead
> of withdrawing money to their bank, is actually making a high risk
> category purchase, which is why the transaction got flagged. However,
> the agent confirms that the shipping address is a trusted one."

**Bucket:** account_takeover x agent-review x APPROVE (trusted shipping
resolves ambiguity)

**Match status:** Matched -- **T30146**.

---

## Bonus (not separately dictated as a story, but kept as a contrast case) -- CURRENTLY BROKEN, curation item

Same shape as 2A but with ~85% of balance moved instead of ~32% -- device
trust alone should NOT be enough to resolve that; the agent should
escalate rather than approve even with a trusted device.

**Match status:** BROKEN. T12994 demonstrated this at one point, but went
stale from a later generator regeneration and was never re-verified --
that was Claude's process error, caught only when refreshing case files
with the new tool-call-reasoning format (T12994 is now a different,
uninteresting $64.36 transaction with no dormancy trigger). On trying to
find a replacement: **this scenario currently has NO matching real row at
all** -- in the present dataset, every legitimate self-withdrawal with the
dormancy + same-day-password-change trigger tops out around ~47% of
balance drained; above that threshold, every matching row is confirmed
`account_takeover` fraud. Logged as a curation item (needs a hand-curated
legitimate row with a large-percentage drain to actually demonstrate the
"device trust isn't an automatic override" point).

---

## Story 3 -- ATO reject via agent: new device/location + purchase + shipping mismatch

> "This could be a case where the account is flagged for ATO because maybe
> there's a login from a new device. And let's say there is no dormancy.
> However, the device ID is new, and the location is new. And let's say
> the shipping address is different from the regular user address. And
> let's say they are purchasing electronics. The time of day is regular.
> And let's say there was no password change. The agent pulls the device
> ID data, finds it to be jailbroken and high risk. And then also inspects
> the shipping address/billing address distance, finds it to be far off,
> and then rejects it for suspected ATO."

**Bucket:** account_takeover x agent-review x REJECT (device intel +
address distance both corroborate)

**Match status:** NOT MATCHED -- confirmed structurally impossible in the
current dataset. The generator hard-codes `account_takeover` archetype to
only ever produce "Send Money" or "Withdraw to Bank" transactions, never
"Pay for Purchase" -- so an ATO case that's a purchase (with a shipping
address to check) cannot exist today. **Curation item.**

---

## Story 4 -- ATO reject via agent: velocity spike, small individual amounts

> "Rather than making any dataset changes right now, let's say that it is a
> send money transaction, sending to a new recipient. And let's say there
> are multiple transfers (velocity spike), but individual transaction
> amount in small, say $34."

**Bucket:** account_takeover x agent-review x REJECT (velocity spike,
small-dollar transfers rather than one large drain)

**Match status:** NOT MATCHED -- closest real case (T15147) has the
right device/location/no-dormancy shape but is a single isolated
transaction, not a burst. Confirmed structurally impossible: ATO amount is
generated as 40-90% of the account's own balance in one transaction
(minimum observed amount across the whole dataset is $97.07, since no ATO
account has a balance under $212), and the archetype's internal
"elevated velocity" variable only nudges fraud probability -- it never
actually generates the extra burst transactions as visible rows.
**Curation item.**

---

## Story 5 -- SFI auto-reject: card testing on a brand-new account

> "Stolen financial case where it is auto rejected. Let's say this is a
> relatively new account. Within twenty days of account creation, or
> within a few days of account creation, there are multiple transactions
> on the account, and a lot of them getting declined by the issuer. Let's
> say eighty to ninety percent of them getting declined. And all these are
> small amounts, which is representative of a card testing attack."

**Bucket:** stolen_funding_instrument x auto-reject (routing-level)

**Match status:** NOT MATCHED -- structurally impossible. No account in
the dataset is younger than 44 days (generator floors `account_open_days_ago`
at `random.randint(30, 2000)`); additionally, declines don't currently
materialize as a linked burst of multiple rows the way a real card-testing
attack would. **Curation item** (shares the account-age-floor root cause
with Story 7 below).

---

## Story 6 -- SFI agent-review, resolves to APPROVE via email check

> "About the stolen financial case, which is due to the agent, and the
> agent actually approves it. So this could be a case where there's a
> relatively new account, and they are suddenly making a transaction with
> their linked card, and that transaction amount is a high risk category --
> let's say a gift card -- and it gets to the agent. The agent runs an
> email check, finds it to be low risk and an old email, and therefore
> approves the transaction... I'm assuming the digital gift card goes to
> the email. So if the email rating is low and email age is high, then I
> think in favor of approving it and cites those factors as the reasons."

**Bucket:** stolen_funding_instrument x agent-review x APPROVE (email check
resolves ambiguity)

**Match status:** PARTIALLY MATCHED -- **T43087** gets the mechanism right
(fresh card + Gift Cards + old/low-risk email -> approve), but Mansi
explicitly flagged the account age (196 days) is wrong for this story: the
whole point is a *new* account with **no real baseline** for
`lookup_user_history` to lean on, so the email check has to carry the
decision essentially alone. T43087 instead gets resolved primarily by a
solid trusted-device/location/recipient baseline, which is a different
(and less interesting) story. **Curation item** (needs account age < 30
days -- same generator floor issue as Story 5).

---

## Story 7 -- SFI agent-review, resolves to REJECT via email + shipping distance

> "Let's talk about a stolen financial case which is rejected by the agent.
> Let's say a similar base. However, when the agent queries the email
> data, they find that the email is new and the risk rating is high. Also,
> the purchased amount is going to a shipping address which is, let's say,
> six hundred miles away."

**Bucket:** stolen_funding_instrument x agent-review x REJECT (email +
address distance both corroborate)

**Match status:** Matched -- **T7917** (fresh funding instrument, Electronics,
email 23 days old/High risk, shipping Chicago->San Francisco/cross-region).
Reject at 82%. Note: this case came with two signals beyond what Mansi
described (a same-amount decline ~1hr earlier from the same device, and a
trusted device/location baseline that the agent correctly did NOT let wash
out the reject) -- worth being aware these are bonus texture, not things
Mansi specifically asked for.

---

## Story 8 -- Policy-only escalate (clean transaction, flagged purely on dollar amount)

> "Another possibility could be a policy case where everything looks fine,
> but it is flagged for review because of a very high dollar amount
> associated with the purchase. It could be a send money transaction
> also."

**Bucket:** policy-triggered escalate, no underlying ATO/SFI signal

**Match status:** Matched -- **T30676** (Send Money, $1,974.19, an
established account/device/location/recipient, routing reason literally
`policy_only` -- raw-evidence tier is `low`, the ONLY reason it reached the
agent at all is the $500 threshold). Agent approves at 82%, explicitly
reasoning that the amount is the only real risk signal and it's outweighed
by a strong behavioral match to the account's own confirmed baseline --
exactly the "policy trigger isn't evidence of risk" behavior this story
was meant to showcase.

---

## Story 9 -- SFI agent-review, resolves to ESCALATE (genuinely mixed evidence)

Framed by Claude, confirmed by Mansi ("I am good with your profiling"):
fresh-ish funding instrument + high-risk category purchase (real signal),
but device/location both trusted, email medium/ambiguous risk rather than
clearly bad, no clear rebuttal either way -- genuinely torn.

**Bucket:** stolen_funding_instrument x agent-review x ESCALATE

**Match status:** Matched -- **T8942** (Gift Cards, 6-day-old linked credit
card, Medium-risk 33-day-old email, a same-afternoon prior decline, but
trusted device/location/recipient and a typical-sized amount). Escalate at
55%.

---

## Story 10 -- Auto-approve, genuinely clean transaction

> "One is genuinely low risk legitimate transaction that never reaches the
> agent."

**Bucket:** auto-approve (routing-level, no signals)

**Match status:** Matched -- **T15996** ($412.49 Send Money to an
established recipient, zero raw-evidence signals).

---

## Story 11 -- Auto-approve, friendly fraud that slips through

> "The second could be some sort of friendly fraud where it looks
> innocuous on the face and hence slipped under our radars, but then
> later on came back with a chargeback and hence a fraud flag and hence a
> fraud flag equal to one."

**Bucket:** auto-approve (routing-level, no signals) but `Fraudulent=1` --
a genuine system-limitation case

**Match status:** Matched -- **T11117** ($112.40 Groceries purchase,
billing=shipping, established recipient, zero raw-evidence signals,
confirmed fraud via later chargeback, True_Fraud_Probability=0.0417 even
at generation time -- i.e. genuinely nothing to catch). This concept
turned out to already be deliberately built into the generator (~30% of
confirmed fraud rows are `True_Archetype=none` "unexplained" fraud by
design) rather than needing new curation.

---

## Open buckets not yet covered by any story

- Story 8 (policy-only escalate) is dictated but not yet searched/matched.

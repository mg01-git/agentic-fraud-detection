# Dataset Schema Reference

Quick-reference for `paypal_transactions.csv`. Full backstory/design reasoning lives in `METHODOLOGY.md`; results live in `FINDINGS.md`. This file is just "what does each column mean, and what does a real row look like."

## Columns

| Column | Meaning |
|---|---|
| Transaction_ID | Unique ID for the row |
| User_ID | Which PayPal account this belongs to |
| Day_Number | Day within our simulated 180-day observation window (1–180) |
| Hour_of_Day | Hour of day, 0–23 |
| Transaction_Type | Send Money / Pay for Purchase / Withdraw to Bank / Receive Money |
| Transaction_Amount | Dollar amount |
| Purchase_Category | Only populated for Pay for Purchase: Electronics, Gift Cards, Travel, Groceries, Subscriptions, or Retail/Other. Electronics/Gift Cards/Travel are the "high resale value" high-risk categories. |
| Funding_Source | PayPal Balance / Linked Bank Account / Linked Debit Card / Linked Credit Card, or "N/A (Incoming)" for Receive Money |
| Funding_Instrument_Age_Days | How long the linked instrument funding THIS transaction has been attached to the account. Only populated when Funding_Source is a linked bank/card (blank for PayPal Balance / incoming). |
| Device_ID | Unique ID of the specific physical device used. Each user owns a small set of real devices (usually 1-3); a device ID never seen before on this account is the real signal — NOT the device type (see Device_Used). |
| Device_Used | Mobile / Desktop / Tablet — the TYPE of the device used. Switching types is normal user behavior (phone one day, laptop the next) and is NOT itself a fraud signal — only an unrecognized Device_ID is. |
| Email | The account's email ID (e.g. `user1234@gmail.com`). This is the ONLY email-related field in the main file — age/risk data deliberately lives only in the separate `email_risk_data.csv` reference table (see below), representing a costly third-party lookup the agent queries selectively rather than a free column. |
| IP_Location | Geolocation inferred from the IP/session at the time of the transaction (not a billing/shipping address) |
| Counterparty_ID | The recipient (outgoing) or sender (incoming); "SELF-BANK" specifically for Withdraw to Bank |
| Is_New_Recipient | True/False — only populated for Send Money / Pay for Purchase (blank otherwise, since Withdraw to Bank and Receive Money don't involve "choosing a recipient") |
| Withdrawal_Destination_Bank_Age_Days | Only populated for Withdraw to Bank — how long the destination bank account has been linked |
| Account_Age_Days | Age of the PayPal account itself, as of this transaction |
| Days_Since_Last_Activity | Gap since this user's previous transaction (or, for their very first transaction in our window, a realistic estimate of time since unknown prior activity — not forced to 0, though 0 is a legitimate occasional value) |
| Previous_Fraudulent_Transactions | This user's confirmed prior fraud count (account-level, not instrument-level — see METHODOLOGY.md for why), as of this transaction |
| Number_of_Transactions_Last_24H | Recent transaction velocity |
| Decline_Rate_Last_24H | Recent decline rate (issuer-side declines) |
| Transaction_Status | Approved / Declined — the issuer's real-time authorization decision |
| Fraudulent | The label — CONFIRMED fraud (1) or not (0). Ground truth; not something the agent computes itself. |

**Not in this file:** `ground_truth_HIDDEN.csv` additionally has `True_Archetype` (account_takeover / stolen_funding_instrument / none) and `True_Contributing_Factors` — for OUR evaluation use only, never fed to the agent.

## Reference tables (separate files, queried selectively by the agent)

These deliberately live OUTSIDE `paypal_transactions.csv` — they represent external, costly third-party data sources a real fraud system would only look up when a case warrants it, not free columns sitting on every row. Each maps one-to-one to one archetype: device intel corroborates/refutes account_takeover suspicion, email intel corroborates/refutes stolen_funding_instrument suspicion.

### `device_threat_intel.csv` (keyed by `Device_ID`)

| Column | Meaning |
|---|---|
| Device_ID | Matches `Device_ID` in the main file |
| Is_Jailbroken | True/False |
| Device_Threat_Rating | Low / Medium / High |

A device never seen before on an account (`account_takeover`'s attacker device) has an ELEVATED but not certain jailbroken rate (~40% vs. a ~4% baseline for a user's own known devices) — real users occasionally jailbreak their own phones for non-fraud reasons, so this is a probabilistic signal, not a rule. It's wired into the underlying fraud probability (not just descriptive): among `account_takeover` cases the risk model already scores as high-risk, jailbroken-device cases are meaningfully more likely to be confirmed fraud than non-jailbroken ones — see `FINDINGS.md`.

### `email_risk_data.csv` (keyed by `Email`)

| Column | Meaning |
|---|---|
| Email | Matches `Email` in the main file |
| Email_Age_Days | How long this email has been associated with the account (set once at account setup, not per-transaction) |
| Email_Risk_Rating | Low / Medium / High, derived from age with some noise (not a perfectly deterministic function of age, same as a real risk-scoring vendor) |

A newer/higher-risk email (< 90 days) PARTIALLY (not deterministically) skews an account toward `stolen_funding_instrument`-pattern attempts and, independently, toward those attempts actually being confirmed fraud — this is what makes `email_risk_data` a genuine, non-decorative signal for the agent to weigh, and it's also what creates real gray-zone `stolen_funding_instrument` cases (this archetype's other signals otherwise fire as an all-or-nothing bundle with no natural ambiguity). See `METHODOLOGY.md` and `FINDINGS.md`.

## Example rows

### Legitimate transaction

```
Transaction_ID: T25003 | User_ID: 1930 | Day 158, Hour 3 (3am)
Receive Money, $40.62
Device: Mobile | Location: New York | Sender: U8341
Account age: 1,483 days | Days since last activity: 2
Prior confirmed fraud: 6 (old) | Velocity (24h): 1 | Decline rate (24h): 0.10
[reference lookups] Email: user1930941@gmail.com -- age 2,933 days (Low risk) | Device DEV-003155 -- not jailbroken (Low threat)
Status: Approved | Fraudulent: 0
```
Nothing unusual here — Receive Money doesn't even involve choosing a recipient/funding source, device and email are both long-established/low-risk, normal velocity/decline rate. Even a nonzero (but old) prior fraud count doesn't flip this — this is what "boring and legitimate" looks like in the data.

### Fraud example — account_takeover archetype

```
Transaction_ID: T43817 | User_ID: 3434 | Day 14, Hour 20
Send Money, $41.84
Funded by: PayPal Balance
Device: DEV-008159 (Mobile) -- never seen on this account before
Location: Miami | Recipient: U2104 (NEW recipient)
Account age: 1,709 days | Days since last activity: 42
Prior confirmed fraud: 0 | Velocity (24h): 6 | Decline rate (24h): 0.14
[reference lookups] Email: user3434978@yahoo.com -- age 1,316 days (Low risk) | Device DEV-008159 -- JAILBROKEN=True (High threat)
Status: Approved | Fraudulent: 1
[hidden ground truth] archetype=account_takeover
  factors: unrecognized_device_id, ip_location_mismatch, high_amount, jailbroken_device, new_recipient
```
The KEY signal is Device_ID (DEV-008159) never having appeared before on this account — the device TYPE (Mobile) might well match something the user has used before, which is exactly why type alone isn't the signal. Querying `device_threat_intel` on this unrecognized device turns up a second, corroborating signal: it's jailbroken with a High threat rating. Note the account's own email is old and low-risk — this fraud has nothing to do with the email, which is exactly why email risk is wired to `stolen_funding_instrument`, not this archetype.

### Fraud example — stolen_funding_instrument archetype

```
Transaction_ID: T33510 | User_ID: 2617 | Day 160, Hour 14
Pay for Purchase, $126.56, category=Electronics (high-risk category)
Funded by: Linked Credit Card (linked only 2.3 days ago!)
Device: Mobile | Location: Houston | Recipient: U2707 (new)
Account age: 637 days | Days since last activity: 0
Prior confirmed fraud: 2 | Velocity (24h): 11 | Decline rate (24h): 0.63 (!)
[reference lookups] Email: user2617433@gmail.com -- age 33 days (Medium risk) | Device DEV-004291 -- not jailbroken (Low threat)
Status: Approved | Fraudulent: 1
[hidden ground truth] archetype=stolen_funding_instrument
  factors: newly_linked_funding_instrument, high_risk_purchase_category,
           elevated_amount, high_decline_rate, high_24h_velocity, new_high_risk_email
```
Note this user's own device looks totally normal (recognized device, not jailbroken) — the fraud signal here is entirely about the FUNDING INSTRUMENT (linked less than 3 days ago), the purchase category (Electronics), a high recent decline rate consistent with card-testing (many attempts, most declined, this one got through), and a comparatively young/riskier account email (33 days). Querying `device_threat_intel` here would be a wasted lookup — it's `email_risk_data` that actually corroborates this case, which is why the agent should learn to call the right tool for the right archetype rather than always calling both.

**Worth remembering:** matching an archetype pattern does NOT guarantee `Fraudulent=1` — the dataset deliberately includes some archetype-matching transactions that end up labeled legitimate (noise on top of the pattern, so the problem isn't trivially/perfectly separable), and conversely, ~30% of all fraud has no archetype at all (pure unexplained noise fraud — see FINDINGS.md for why this matters for what "good" model performance looks like here).

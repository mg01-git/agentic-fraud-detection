# Case File: T15996 (auto-approved -- never reached the agent)

**Routing decision:** `auto_approve` (risk_tier=low, risk_points=0, no signals matched, policy not triggered)

This transaction never invoked the agent at all -- it's included as a
baseline "boring, correctly-skipped" case, to show what the system does
with the overwhelming majority of real traffic.

## Transaction record

- **Type:** Send Money
- **Amount:** $412.49
- **Account balance before transaction:** $1,324.36
- **Device:** DEV-001977 (Desktop)
- **Location:** Boston
- **New recipient?:** No (established counterparty)
- **Account age (days):** 602
- **Days since last activity:** 13 (no dormancy)
- **Days since password change:** 144
- **Status:** Approved

## Ground truth

- **Fraudulent:** 0
- **True archetype:** none

Nothing here trips any raw-evidence signal (large-%-balance-moved,
dormancy, unusual hour, password-change-after-dormancy, new recipient,
address mismatch, fresh funding instrument) -- an established recipient,
a normal-hours transaction from the account's typical pattern, and an
amount well under the $500 policy-review threshold. Correctly and
uneventfully auto-approved.

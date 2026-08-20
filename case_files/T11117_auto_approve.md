# Case File: T11117 (auto-approved -- never reached the agent; confirmed "friendly fraud")

**Routing decision:** `auto_approve` (risk_tier=low, risk_points=0, no signals matched, policy not triggered)

This transaction also never invoked the agent -- but unlike T15996, it
later turned out to BE fraud (a confirmed chargeback), even though there
was zero transaction-time signal to catch it on. This models "friendly
fraud": a customer disputes a transaction that looked completely normal
when it happened.

## Transaction record

- **Type:** Pay for Purchase
- **Amount:** $112.40
- **Purchase category:** Groceries
- **Account balance before transaction:** $2,687.15
- **Billing location:** Houston
- **Shipping location:** Houston (matches billing)
- **Device:** DEV-001360 (Mobile)
- **Location:** Houston
- **New recipient?:** No (established counterparty)
- **Account age (days):** 909
- **Days since last activity:** 17 (no dormancy)
- **Days since password change:** 126
- **Status:** Approved

## Ground truth

- **Fraudulent:** 1 (confirmed, later disputed)
- **True archetype:** none -- "unexplained" fraud by design (see
  generate_synthetic_data.py's module docstring: ~30% of confirmed fraud
  rows deliberately carry no transaction-time signal at all)
- **True fraud probability (model's internal ground-truth score at
  generation time):** 0.0417 -- i.e. even the data-generation process
  itself considered this case low-risk; there was nothing to catch.

This is the honest ceiling on the current system: no raw-evidence score,
tool, or agent could reasonably have flagged this one. It's a genuine
limitation, not a bug -- worth having in the demo set specifically to be
upfront about it rather than implying the system catches everything.

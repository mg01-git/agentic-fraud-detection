# PayPal Fraud & Compliance Policy — Transaction Review Rules

This is a deliberately small, plain-language policy document, meant to be read by both a human and the Decision Agent's `policy_lookup` tool. It defines MANDATORY business rules — rules that apply regardless of how likely a transaction is judged to be fraudulent by any risk score or model. These are compliance/business decisions, not fraud predictions, and the two are kept separate on purpose (see METHODOLOGY.md / FINDINGS.md for why).

## Rule 1: High-Value Transaction Review Threshold

**Any transaction with `Transaction_Amount >= $500.00` must be escalated for mandatory enhanced review**, regardless of its fraud risk score or any other evidence.

**Rationale:** large-value movements warrant a second look as standard operating procedure, independent of how likely they are to be fraudulent — most transactions above this line are perfectly legitimate, but the business has decided the cost of a brief manual check on all of them is worth it. This mirrors (in spirit, not in exact figure) how real financial institutions apply enhanced-review thresholds separately from fraud-risk modeling.

This threshold ($500) was chosen by inspecting the actual distribution of transaction amounts in this dataset — at $500, about 2% of all transactions trigger it, and of those, roughly 31% turn out to be confirmed fraud and 69% do not. That mix is intentional: the rule should flag a real mix of legitimate and fraudulent activity, not just restate what the risk score already says.

This is the only mandatory policy rule currently defined. Other candidate rules (restricted purchase categories, decline-rate thresholds, newly-linked-instrument thresholds) were considered and deliberately deferred — see `OPEN_ITEMS_FOR_LATER.md` for why.

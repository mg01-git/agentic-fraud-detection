"""
Phase 3 tool: policy_lookup.

A DETERMINISTIC, hard-coded business-rule check -- NOT a fraud-risk
prediction. Mirrors POLICY.md exactly (POLICY.md is the human-readable
source of truth/rationale; this is its executable form -- if the policy
changes, update both together).

Deliberately narrow: a single mandatory rule (high-value transaction
review), chosen specifically because it is INDEPENDENT of the signals
get_risk_score, device_threat_intel, and email_risk_data already cover --
it fires just as often on ordinary legitimate transactions as on fraud
(see POLICY.md for the actual mix). This is what makes it a genuinely
different kind of evidence for the Decision Agent to reason about, not a
restatement of a risk signal under a different name: everything else in
this system is PROBABILISTIC evidence the agent weighs; this is a
CATEGORICAL rule that applies regardless of how likely fraud is.

The agent calls this as ONE of several evidence sources, alongside
get_risk_score / lookup_user_history / device_threat_intel / email_risk_data.
A triggered result forces a MANDATORY escalation -- the agent doesn't get
to override it -- but it still incorporates the result into its written
explanation rather than skipping straight to an output.
"""

HIGH_VALUE_THRESHOLD = 500.00


def policy_lookup(transaction_amount):
    """transaction_amount: float, the transaction's Transaction_Amount.

    Returns a dict describing whether any mandatory policy rule was
    triggered, and if so, which one and what it requires."""
    triggered = transaction_amount >= HIGH_VALUE_THRESHOLD
    return {
        "rule_triggered": triggered,
        "rule_name": "high_value_transaction_review" if triggered else None,
        "mandatory_action": "escalate" if triggered else None,
        "policy_reference": "POLICY.md, Rule 1",
        "explanation": (
            f"Transaction amount ${transaction_amount:.2f} meets or exceeds the "
            f"${HIGH_VALUE_THRESHOLD:.2f} mandatory enhanced-review threshold (POLICY.md, Rule 1)."
        ) if triggered else (
            f"Transaction amount ${transaction_amount:.2f} is below the "
            f"${HIGH_VALUE_THRESHOLD:.2f} mandatory-review threshold; no policy rule triggered."
        ),
    }


if __name__ == "__main__":
    # quick self-check against the actual dataset
    import pandas as pd
    df = pd.read_csv("paypal_transactions.csv")
    gt = pd.read_csv("ground_truth_HIDDEN.csv")
    full = df.merge(gt, on="Transaction_ID")
    flagged = full["Transaction_Amount"].apply(lambda a: policy_lookup(a)["rule_triggered"])
    print(f"Flagged: {flagged.sum()} / {len(full)} ({flagged.mean():.2%})")
    print(f"Fraud rate among flagged: {full.loc[flagged, 'Fraudulent'].mean():.2%}")

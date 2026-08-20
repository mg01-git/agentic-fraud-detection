"""
Phase 3: tiered routing / gating logic.

Decides, for one transaction, whether it can be auto-approved with NO LLM
call, or needs to go to the full Decision Agent. This is the cost-control
layer of the whole system -- the entire point of the agent is wasted if it
runs on every transaction instead of only the ones where its judgment
actually changes the outcome.

TWO PATHS, NOT THREE. An earlier sketch of this system assumed a third,
"very high score -> auto-escalate/reject with no LLM call" tier. Dropped
after actually inspecting the score-band data (see FINDINGS.md): scores
from 0 up to AGENT_REVIEW_SCORE_THRESHOLD are statistically flat (~1.8%
fraud rate the whole way -- no real distinction between "low" and
"medium"), and even the high band above the threshold only reaches ~60%
precision, not confident enough to justify skipping the agent entirely.
So there is no score-alone "certain fraud" tier in this dataset -- every
transaction that reaches the threshold, or that trips a mandatory policy
rule, gets full agent review; only transactions below the threshold with
no policy hit skip the agent.

POLICY HITS ALSO ROUTE TO THE AGENT, NOT A SILENT AUTO-ESCALATE. A
mandatory policy hit (see policy_lookup.py) already fixes the ultimate
decision (escalate) regardless of what the agent concludes -- but the
agent still runs, so a human reviewer gets a written case file explaining
what was checked, rather than a bare "policy triggered" flag with no
context. Deliberate choice, made explicitly (not a cost-minimizing
default) because the review-queue experience matters more here than the
(small -- ~2% of transactions) added LLM cost.
"""

from get_risk_score import get_risk_score
from policy_lookup import policy_lookup

# LOW skips the agent; MEDIUM and HIGH both route to it. Deliberately no
# auto-reject tier, even for HIGH -- get_risk_score is now a raw-evidence-only
# triage filter with no access to the tools (device/email/history/address)
# that actually resolve ambiguity, so it structurally can never be confident
# enough to skip human/agent review, regardless of how many raw signals fire.
AUTO_APPROVE_TIER = "low"


def route_transaction(txn):
    """txn: dict of raw transaction fields.
    Returns a dict describing the routing decision -- NOT the final fraud
    decision itself, which (for agent_review cases) is the Decision
    Agent's job, not this function's."""
    score_result = get_risk_score(txn)
    policy_result = policy_lookup(txn["Transaction_Amount"])

    needs_review = (score_result["risk_tier"] != AUTO_APPROVE_TIER) or policy_result["rule_triggered"]

    return {
        "route": "agent_review" if needs_review else "auto_approve",
        "risk_tier": score_result["risk_tier"],
        "risk_points": score_result["points"],
        "risk_signals": score_result["signals_matched"],
        "policy_triggered": policy_result["rule_triggered"],
        "policy_detail": policy_result if policy_result["rule_triggered"] else None,
        "reason": (
            "policy_and_tier" if (score_result["risk_tier"] != AUTO_APPROVE_TIER and policy_result["rule_triggered"])
            else "policy_only" if policy_result["rule_triggered"]
            else "tier_only" if score_result["risk_tier"] != AUTO_APPROVE_TIER
            else "clear"
        ),
    }


if __name__ == "__main__":
    import pandas as pd
    df = pd.read_csv("paypal_transactions.csv")
    gt = pd.read_csv("ground_truth_HIDDEN.csv")

    routes, reasons = [], []
    for _, row in df.iterrows():
        r = route_transaction(row.to_dict())
        routes.append(r["route"])
        reasons.append(r["reason"])
    df["_route"] = routes
    df["_reason"] = reasons
    full = df.merge(gt, on="Transaction_ID")

    print(full["_route"].value_counts())
    print()
    print(full[full["_route"] == "agent_review"]["_reason"].value_counts())
    print()
    print("Fraud rate by route:")
    print(full.groupby("_route")["Fraudulent"].agg(["mean", "count"]))
    print()
    print("Auto-approve band -- any fraud slipping through with zero LLM review?")
    aa = full[full["_route"] == "auto_approve"]
    print(f"  {aa['Fraudulent'].sum()} fraud rows out of {len(aa)} auto-approved ({aa['Fraudulent'].mean():.3%})")

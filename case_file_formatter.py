"""
Phase 5: readable trace / case-file output.

Formats one Decision Agent run (transaction + tool-call trace + final
decision) into a readable markdown "case file" -- this is the demo
interface we agreed on instead of building a UI (see OPEN_ITEMS_FOR_LATER.md):
walkable, screen-shareable, and it doubles as the tracing/observability
deliverable (Phase 5) rather than being separate work.

Takes exactly what decision_agent.run_decision_agent() already returns --
no new agent logic here, purely presentation.
"""

RAW_EVIDENCE_DISPLAY = [
    ("Transaction_Type", "Type"), ("Transaction_Amount", "Amount"),
    ("Account_Balance_Before_Transaction", "Account balance before transaction"),
    ("Purchase_Category", "Purchase category"), ("Funding_Source", "Funding source"),
    ("Funding_Instrument_Age_Days", "Funding instrument age (days)"),
    ("Billing_Location", "Billing location"), ("Shipping_Location", "Shipping location"),
    ("Device_ID", "Device ID"), ("Device_Used", "Device type"), ("Email", "Email"),
    ("IP_Location", "Location"), ("Is_New_Recipient", "New recipient?"),
    ("Withdrawal_Destination_Bank_Age_Days", "Destination bank age (days)"),
    ("Account_Age_Days", "Account age (days)"), ("Days_Since_Last_Activity", "Days since last activity"),
    ("Days_Since_Password_Change", "Days since password change"),
    # Previous_Fraudulent_Transactions deliberately NOT displayed here --
    # confirmed earlier this session it's not a real predictive signal for
    # either account_takeover or stolen_funding_instrument (flat fraud rate
    # with/without it, within either archetype -- see get_risk_score.py's
    # comment). Still technically visible to the agent via RAW_EVIDENCE_FIELDS
    # (removing it there would be a bigger behavior change than "remove it
    # from the display"), but not surfaced in the case file to avoid a human
    # reviewer over-indexing on a number that doesn't actually mean anything.
    ("Transaction_Status", "Status"),
]

DECISION_EMOJI = {"approve": "✅ APPROVE", "reject": "⛔ REJECT", "escalate": "🔺 ESCALATE"}


def format_case_file(txn, decision, trace, route_info=None):
    """txn: raw transaction dict. decision: submit_decision input dict.
    trace: list of {tool, input, output} dicts. route_info: optional
    output of routing.route_transaction(), for context on why this case
    reached the agent at all."""
    lines = []
    lines.append(f"# Case File: {txn['Transaction_ID']}")
    lines.append("")

    if route_info:
        lines.append(f"**Routed to agent because:** `{route_info['reason']}` "
                      f"(risk_tier={route_info['risk_tier']}, risk_points={route_info['risk_points']}, "
                      f"policy_triggered={route_info['policy_triggered']})")
        if route_info.get("risk_signals"):
            lines.append(f"Raw-evidence signals matched: {', '.join(route_info['risk_signals'])}")
        lines.append("")

    lines.append("## Decision")
    lines.append("")
    lines.append(f"### {DECISION_EMOJI.get(decision['decision'], decision['decision'].upper())} "
                  f"(confidence: {decision['confidence']:.0%})")
    lines.append("")
    if decision.get("risk_factors"):
        lines.append("**⚠️ Risk factors:**")
        for f in decision["risk_factors"]:
            lines.append(f"- {f}")
        lines.append("")
    if decision.get("mitigating_factors"):
        lines.append("**✅ Mitigating factors:**")
        for f in decision["mitigating_factors"]:
            lines.append(f"- {f}")
        lines.append("")
    lines.append("**Why this decision:**")
    lines.append("")
    lines.append(decision["explanation"])
    lines.append("")

    lines.append("## Transaction record")
    lines.append("")
    for field, label in RAW_EVIDENCE_DISPLAY:
        val = txn.get(field, "")
        if val not in (None, "", "nan") and str(val).lower() != "nan":
            lines.append(f"- **{label}:** {val}")
    lines.append("")

    lines.append("## Tool calls")
    lines.append("")
    if not trace:
        lines.append("_No tools called -- the agent judged the raw evidence and pre-computed context above sufficient._")
    prev_reasoning = None
    for i, step in enumerate(trace, 1):
        lines.append(f"**{i}. `{step['tool']}`**")
        lines.append("")
        # A single reasoning text can precede a whole batch of tool calls made
        # in the same turn (e.g. "check history AND check distance") -- don't
        # repeat it verbatim under every tool in that batch, only show it once.
        if step.get("reasoning") and step["reasoning"] != prev_reasoning:
            lines.append(f"_{step['reasoning']}_")
            lines.append("")
        prev_reasoning = step.get("reasoning")
        for k, v in step["output"].items():
            lines.append(f"- **{k}:** {v}")
        lines.append("")

    return "\n".join(lines)


def write_case_file(txn, decision, trace, route_info=None, out_dir="case_files"):
    import os
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{txn['Transaction_ID']}.md")
    with open(path, "w") as f:
        f.write(format_case_file(txn, decision, trace, route_info))
    return path


if __name__ == "__main__":
    import pandas as pd
    from decision_agent import run_decision_agent
    from routing import route_transaction
    from mock_llm_client import MockLLMClient

    df = pd.read_csv("paypal_transactions.csv")
    client = MockLLMClient()

    # use a couple of the actual curated demo cases
    for txn_id in ["T43817", "T33510", "T8267"]:
        txn = df[df["Transaction_ID"] == txn_id].iloc[0].to_dict()
        route_info = route_transaction(txn)
        decision, trace = run_decision_agent(txn, client)
        path = write_case_file(txn, decision, trace, route_info)
        print(f"Wrote {path}")

"""
Phase 3: mock LLM client for testing the Decision Agent's tool-use loop
BEFORE the real Anthropic API key is available.

NOT a substitute for the real model in any demo/eval sense -- it uses
simple, deterministic heuristics instead of real reasoning, and its
"explanation" text is a canned placeholder. Its only job is to exercise
decision_agent.py's plumbing: correct message threading, correct tool
dispatch, correct termination, well-formed final output matching the
submit_decision schema -- so that swapping in the real client later is a
one-line change, not a debugging session.

Implements the SAME interface real_llm_client.py implements:
.create_message(system, messages, tools) -> {"content": [...], "stop_reason": ...},
matching a simplified subset of the real Anthropic Messages API's response shape.

Mirrors the CURRENT tool set and the system prompt's tool-calling-judgment
guidance (see decision_agent._build_system_prompt) -- get_risk_score and
policy_lookup are no longer tools (folded into system-prompt context), and
lookup_user_history/address_distance_lookup are called conditionally, not
unconditionally, same as a real model following the prompt's instructions
would.
"""

import json
import re

_id_counter = [0]


def _next_id():
    _id_counter[0] += 1
    return f"mock_tool_{_id_counter[0]}"


def _tool_use_response(name, tool_input):
    return {
        "content": [{"type": "tool_use", "id": _next_id(), "name": name, "input": tool_input}],
        "stop_reason": "tool_use",
    }


def _extract_raw_evidence(system):
    """Raw evidence is one JSON object embedded in the system prompt,
    followed by more text (the pre-computed routing context) -- use
    raw_decode so trailing text after the object doesn't break parsing."""
    marker = "Transaction record:\n"
    idx = system.index(marker) + len(marker)
    obj, _ = json.JSONDecoder().raw_decode(system[idx:])
    return obj


def _extract_routing_context(system):
    """Parses the 'Why this case was routed to you' block -- this is
    pre-computed context now (get_risk_score/policy_lookup are no longer
    tools), not something available in tool_results."""
    tier_match = re.search(r"Raw-evidence risk tier: (\w+) \((\d+) points\)", system)
    policy_triggered = "Policy check: TRIGGERED" in system
    return {
        "risk_tier": tier_match.group(1) if tier_match else "low",
        "risk_points": int(tier_match.group(2)) if tier_match else 0,
        "policy_triggered": policy_triggered,
    }


def _extract_tool_results(messages):
    """Walk the conversation so far and return {tool_name: output_dict} for
    every tool that's already been called (last result wins)."""
    id_to_name = {}
    results = {}
    for msg in messages:
        if msg["role"] == "assistant":
            for block in msg["content"]:
                if block["type"] == "tool_use":
                    id_to_name[block["id"]] = block["name"]
        elif msg["role"] == "user" and isinstance(msg["content"], list):
            for block in msg["content"]:
                if block.get("type") == "tool_result":
                    name = id_to_name.get(block["tool_use_id"])
                    if name:
                        results[name] = json.loads(block["content"])
    return results


class MockLLMClient:
    """Deterministic stand-in for the real Anthropic client. Follows the
    same tool-calling judgment the real prompt asks for, not a fixed
    unconditional call order:
      - lookup_user_history: only if Account_Age_Days >= 30
      - device_intel_lookup: only if lookup_user_history was called AND
        came back with an unrecognized device
      - email_risk_lookup: only if the funding instrument looks freshly
        linked (< 15 days, matching get_risk_score's own threshold)
      - address_distance_lookup: only if Transaction_Type is
        'Pay for Purchase' AND Billing_Location/Shipping_Location are set
      - submit_decision once all applicable tools have been called
    """

    def create_message(self, system, messages, tools):
        raw = _extract_raw_evidence(system)
        routing = _extract_routing_context(system)
        results = _extract_tool_results(messages)

        account_age = raw.get("Account_Age_Days")
        account_established = account_age not in (None, "") and float(account_age) >= 30
        if account_established and "lookup_user_history" not in results:
            return _tool_use_response("lookup_user_history", {})

        device_unrecognized = (
            "lookup_user_history" in results
            and not results["lookup_user_history"]["is_current_device_known"]
        )
        if device_unrecognized and "device_intel_lookup" not in results:
            return _tool_use_response("device_intel_lookup", {})

        fi_age = raw.get("Funding_Instrument_Age_Days")
        funding_looks_new = fi_age not in (None, "") and not (isinstance(fi_age, float) and fi_age != fi_age) and float(fi_age) < 15
        if funding_looks_new and "email_risk_lookup" not in results:
            return _tool_use_response("email_risk_lookup", {})

        is_purchase = raw.get("Transaction_Type") == "Pay for Purchase"
        has_addresses = bool(raw.get("Billing_Location")) and bool(raw.get("Shipping_Location"))
        if is_purchase and has_addresses and "address_distance_lookup" not in results:
            return _tool_use_response("address_distance_lookup", {})

        risk_tier = routing["risk_tier"]
        policy_triggered = routing["policy_triggered"]
        decision = "escalate" if (risk_tier == "high" or policy_triggered) else (
            "escalate" if risk_tier == "medium" else "approve"
        )

        risk_factors = []
        if risk_tier != "low":
            risk_factors.append(f"raw_evidence_risk_tier={risk_tier} ({routing['risk_points']} points)")
        if policy_triggered:
            risk_factors.append("policy_rule_triggered")
        if device_unrecognized:
            risk_factors.append("device_unrecognized")
        if funding_looks_new:
            risk_factors.append("funding_instrument_recently_linked")

        mitigating_factors = []
        if "lookup_user_history" in results and results["lookup_user_history"]["is_current_device_known"]:
            mitigating_factors.append("device_recognized_against_true_baseline")
        if not risk_factors:
            mitigating_factors.append("no_raw_evidence_risk_signals_and_policy_not_triggered")

        decision_input = {
            "decision": decision,
            "confidence": 0.6,
            "risk_factors": risk_factors,
            "mitigating_factors": mitigating_factors,
            "explanation": (
                "[MOCK CLIENT -- plumbing test only, not real reasoning] "
                f"risk_tier={risk_tier}, policy_triggered={policy_triggered}, "
                f"device_unrecognized={device_unrecognized}, funding_looks_new={funding_looks_new}."
            ),
        }
        return _tool_use_response("submit_decision", decision_input)


if __name__ == "__main__":
    import pandas as pd
    from decision_agent import run_decision_agent
    from routing import route_transaction

    df = pd.read_csv("paypal_transactions.csv")
    client = MockLLMClient()

    tested = 0
    for _, row in df.iterrows():
        txn = row.to_dict()
        route = route_transaction(txn)
        if route["route"] != "agent_review":
            continue
        decision, trace = run_decision_agent(txn, client, verbose=False)
        tools_called = [t["tool"] for t in trace]
        print(f"{txn['Transaction_ID']}: tools={tools_called} -> {decision['decision']} (conf={decision['confidence']})")
        tested += 1
        if tested >= 8:
            break
    print(f"\n{tested} agent-review transactions ran through the full loop with no errors.")

"""
Judge LLM -- a minimal first spike, per Mansi's request to pressure-test the
concept on one existing case before deciding whether/how to build it into
the eval harness properly.

Distinct from the Decision Agent in one crucial way: the judge SEES the
hidden ground truth (True_Archetype, True_Fraud_Probability,
True_Contributing_Factors, Fraudulent) that the Decision Agent never has
access to. Its job is not to re-decide the case -- it's to critique
whether the Decision Agent's OWN decision and reasoning were sound, given
what actually happened. This mirrors the kind of critique Mansi has been
giving by hand all session (e.g. "why is prior fraud still being cited",
"the shipping check should have been called here") -- the point of this
spike is to see whether an LLM judge can produce that same caliber of
critique on its own, not just agree/disagree with the verdict.

Deliberately NOT wired into decision_agent.py or the eval harness yet --
this is a standalone spike script Mansi can run against one case at a
time and read the output of directly.
"""

import json

JUDGE_TOOL_SCHEMA = [
    {
        "name": "submit_verdict",
        "description": "Submit your critique of the Decision Agent's handling of this case.",
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome_verdict": {
                    "type": "string",
                    "enum": ["correct", "defensible_but_wrong", "concerning"],
                    "description": (
                        "correct: the decision matches what the ground truth supports (including "
                        "ESCALATE being correct when evidence was genuinely mixed, even if ground truth "
                        "later resolved one way -- escalate isn't a cop-out if the case really was "
                        "ambiguous given only what the agent could see). "
                        "defensible_but_wrong: reasonable given available evidence, but ground truth "
                        "disagrees, and nothing in the agent's OWN process was actually flawed. "
                        "concerning: the decision is wrong AND the agent's reasoning process itself has "
                        "a real flaw (misused evidence, missed a tool it should have called, ignored a "
                        "signal it had) -- not just an unlucky case."
                    ),
                },
                "reasoning_quality_score": {
                    "type": "integer",
                    "description": "1-5. 5 = evidence-weighting is precise and the explanation would "
                    "satisfy a skeptical human reviewer. 1 = evidence is misused or contradicted by the "
                    "agent's own explanation.",
                },
                "evidence_issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific problems with how evidence was used: cited something "
                    "non-predictive as if it were meaningful, ignored a signal it had access to, drew a "
                    "conclusion the tool output doesn't actually support, etc. Empty list if none.",
                },
                "missed_tool_calls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tools the agent did NOT call but should have, given what it knew at "
                    "the time (not given the ground truth) -- e.g. 'should have called "
                    "address_distance_lookup given the fresh funding instrument + high-risk category'. "
                    "Empty list if none.",
                },
                "critique": {
                    "type": "string",
                    "description": "2-4 sentences, direct and specific -- the kind of feedback a sharp "
                    "fraud-ops lead would give on this case file, not generic praise.",
                },
            },
            "required": ["outcome_verdict", "reasoning_quality_score", "evidence_issues", "missed_tool_calls", "critique"],
        },
    }
]


def _build_judge_prompt(txn, decision, trace, ground_truth):
    trace_summary = []
    for step in trace:
        entry = {"tool": step["tool"], "output": step["output"]}
        if step.get("reasoning"):
            entry["agent_reasoning_before_call"] = step["reasoning"]
        trace_summary.append(entry)

    return (
        "You are a senior fraud-operations lead auditing a junior fraud-review agent's decision on a "
        "single transaction. You have access to information the agent did NOT have: the actual ground "
        "truth outcome. Your job is to critique whether the agent's decision and reasoning process were "
        "sound -- not just whether it happened to land on the right label. An ESCALATE can be the CORRECT "
        "call even when ground truth later resolves cleanly one way, if the evidence available to the "
        "agent at decision time was genuinely mixed. Conversely, an agent can land on the technically "
        "correct label for the wrong or sloppy reasons -- call that out too.\n\n"
        f"The agent's decision:\n"
        f"- Verdict: {decision['decision'].upper()} (confidence {decision['confidence']:.0%})\n"
        f"- Risk factors cited: {decision.get('risk_factors', [])}\n"
        f"- Mitigating factors cited: {decision.get('mitigating_factors', [])}\n"
        f"- Explanation: {decision['explanation']}\n\n"
        f"Tools the agent called, its own stated reasoning for calling each, and what each returned:\n"
        f"{json.dumps(trace_summary, indent=2, default=str)}\n\n"
        f"Transaction record the agent was working from:\n"
        f"{json.dumps({k: v for k, v in txn.items() if k != 'Transaction_ID'}, indent=2, default=str)}\n\n"
        f"GROUND TRUTH (the agent never saw this):\n"
        f"- Fraudulent: {ground_truth['Fraudulent']}\n"
        f"- True archetype: {ground_truth['True_Archetype']}\n"
        f"- True fraud probability (at generation time): {ground_truth['True_Fraud_Probability']}\n"
        f"- True contributing factors: {ground_truth.get('True_Contributing_Factors', 'n/a')}\n\n"
        "Call submit_verdict with your critique."
    )


def run_judge(llm_client, txn, decision, trace, ground_truth, max_retries=2):
    system = _build_judge_prompt(txn, decision, trace, ground_truth)
    messages = [{"role": "user", "content": "Audit this case now."}]

    for attempt in range(max_retries + 1):
        response = llm_client.create_message(system=system, messages=messages, tools=JUDGE_TOOL_SCHEMA)
        tool_calls = [b for b in response["content"] if b["type"] == "tool_use"]
        if tool_calls:
            return tool_calls[0]["input"]
        # single-shot, no forced tool_choice plumbed through the shared client
        # interface -- nudge it to actually call the tool rather than just
        # replying in prose, same pattern as decision_agent's retry handling.
        messages.append({"role": "assistant", "content": response["content"]})
        messages.append({
            "role": "user",
            "content": "You must call submit_verdict with your critique -- do not just respond in prose.",
        })
    raise RuntimeError(f"Judge did not call submit_verdict after {max_retries + 1} attempts")


if __name__ == "__main__":
    import pandas as pd
    from decision_agent import run_decision_agent
    from routing import route_transaction
    from real_llm_client import RealLLMClient

    txns = pd.read_csv("paypal_transactions.csv")
    gt = pd.read_csv("ground_truth_HIDDEN.csv")

    txn_id = "T8942"  # SFI-escalate case -- genuinely mixed evidence, good judge test
    txn = txns[txns["Transaction_ID"] == txn_id].iloc[0].to_dict()
    ground_truth = gt[gt["Transaction_ID"] == txn_id].iloc[0].to_dict()

    client = RealLLMClient()
    route_info = route_transaction(txn)
    decision, trace = run_decision_agent(txn, client)

    print(f"=== {txn_id} -- Decision Agent's verdict ===")
    print(f"{decision['decision'].upper()} ({decision['confidence']:.0%})")
    print(decision["explanation"])
    print()

    verdict = run_judge(client, txn, decision, trace, ground_truth)
    print(f"=== Judge's critique ===")
    print(json.dumps(verdict, indent=2))

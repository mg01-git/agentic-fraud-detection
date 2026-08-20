"""
Phase 3: the real Anthropic client, swapped in for mock_llm_client.py.

Implements the EXACT SAME interface as MockLLMClient --
.create_message(system, messages, tools) -> {"content": [...], "stop_reason": ...}
-- so decision_agent.py needed zero changes to use this instead of the
mock. That was the whole point of separating them (see METHODOLOGY.md).

Reads ANTHROPIC_API_KEY from .env in this directory -- NOT from any file
that gets delivered or synced elsewhere. .env is excluded from anything
sent to the user; it never leaves this session.
"""

import os
import anthropic

DEFAULT_MODEL = "claude-sonnet-5"

_client_cache = None


def _load_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    with open(".env") as f:
        for line in f:
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("ANTHROPIC_API_KEY not found in environment or .env")


def _get_client():
    global _client_cache
    if _client_cache is None:
        _client_cache = anthropic.Anthropic(api_key=_load_api_key())
    return _client_cache


class RealLLMClient:
    def __init__(self, model=DEFAULT_MODEL, max_tokens=2048):
        self.model = model
        self.max_tokens = max_tokens

    def create_message(self, system, messages, tools):
        response = _get_client().messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )
        content = []
        for block in response.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        return {"content": content, "stop_reason": response.stop_reason}


if __name__ == "__main__":
    import pandas as pd
    from decision_agent import run_decision_agent
    from routing import route_transaction

    df = pd.read_csv("paypal_transactions.csv")
    client = RealLLMClient()

    txn = df[df["Transaction_ID"] == "T43817"].iloc[0].to_dict()
    route_info = route_transaction(txn)
    decision, trace = run_decision_agent(txn, client, verbose=True)
    print("\nDECISION:", decision)
    print("\nTOOLS CALLED:", [t["tool"] for t in trace])

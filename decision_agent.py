"""
Phase 3: the Decision Agent's tool-use loop.

This module is CLIENT-AGNOSTIC on purpose: run_decision_agent() takes an
`llm_client` object with a single method,
    llm_client.create_message(system, messages, tools) -> {"content": [...], "stop_reason": ...}
matching (a simplified subset of) the real Anthropic Messages API's shape.
mock_llm_client.py and the real Anthropic client both implement this same
interface, so swapping one for the other requires touching NOTHING in this
file -- that's the entire point of separating them (see METHODOLOGY.md).

EVIDENCE MODEL (see chat/METHODOLOGY.md Step 9 for the full reasoning):
the agent always has the full raw transaction row in its system prompt --
amount, purchase category, funding source and its age, decline rate,
velocity, new-recipient flag, hour, destination bank age are all already
visible with zero tool calls, the same way a human analyst would have the
full record open. The tools below exist ONLY for evidence that is NOT
already in the row: a statistical score, this user's own comparative
history, and two pieces of external third-party data. The agent is
instructed to cite the raw row directly wherever relevant, not just tool
outputs.

The final "tool" (submit_decision) is how the agent's structured output is
collected -- a common pattern for forcing a well-formed final answer out
of a tool-use loop, rather than parsing free text.
"""

import json
import re
from get_risk_score import get_risk_score
from lookup_user_history import lookup_user_history
from policy_lookup import policy_lookup
from device_intel_lookup import device_intel_lookup
from email_risk_lookup import email_risk_lookup
from address_distance_lookup import address_distance_lookup

MAX_TOOL_ITERATIONS = 8

TOOL_SCHEMAS = [
    {
        "name": "lookup_user_history",
        "description": "Live, strictly time-respecting query of this user's own prior transactions: TRUE recognized device/home-location (from account creation, not window-reconstructed), typical amount, distinct recipients, live 24h transaction count/declined count, thin-history flag, an estimated lifetime transaction count (real observed count plus account-tenure baseline), and a summary of the most recent prior transaction.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "device_intel_lookup",
        "description": "Third-party device reputation lookup (jailbroken status, threat rating) for the Device_ID on this transaction. Most relevant when the device is unrecognized (possible account_takeover) -- calling it on an already-recognized device is rarely useful.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "email_risk_lookup",
        "description": "Third-party email risk lookup (age, risk rating) for the Email on this account. Most relevant when other signals suggest stolen_funding_instrument (newly-linked funding instrument, high-risk purchase category, high decline rate) -- calling it for an account_takeover-looking case is rarely useful.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "address_distance_lookup",
        "description": "Mimics an expensive real-time distance computation between this transaction's Billing_Location and Shipping_Location (coarse bucket: same_city / same_region / cross_region). Only meaningful for Transaction_Type == 'Pay for Purchase' -- Billing_Location/Shipping_Location are not populated for any other transaction type, so calling this for a non-purchase transaction is never useful.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "submit_decision",
        "description": "Submit the FINAL decision. Call this exactly once, after gathering whatever evidence is actually needed -- not before, and not after calling the same tool twice.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["approve", "reject", "escalate"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "risk_factors": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Short bullet-style points (not sentences) -- specific, checkable facts that push toward suspicion.",
                },
                "mitigating_factors": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Short bullet-style points -- specific, checkable facts that push toward this being legitimate. Can be empty ONLY if genuinely nothing mitigates.",
                },
                "explanation": {
                    "type": "string",
                    "description": "2-4 sentences MAX, not a long paragraph. Must explicitly state WHY this decision follows from the balance of risk_factors vs mitigating_factors -- for escalate specifically, name the actual tension (which evidence conflicts with which), not just restate the risk factors.",
                },
            },
            "required": ["decision", "confidence", "risk_factors", "mitigating_factors", "explanation"],
        },
    },
]

# transaction fields the agent always sees, with zero tool calls -- see
# module docstring. Kept as a constant so the "raw evidence" surface is
# explicit and auditable, not implicit in whatever get_risk_score happens
# to use internally.
RAW_EVIDENCE_FIELDS = [
    "Transaction_ID", "User_ID", "Day_Number", "Hour_of_Day", "Transaction_Type",
    "Transaction_Amount", "Purchase_Category", "Funding_Source", "Funding_Instrument_Age_Days",
    "Device_ID", "Device_Used", "Email", "IP_Location", "Counterparty_ID", "Is_New_Recipient",
    "Withdrawal_Destination_Bank_Age_Days", "Account_Age_Days", "Days_Since_Last_Activity",
    "Previous_Fraudulent_Transactions", "Account_Balance_Before_Transaction",
    "Billing_Location", "Shipping_Location", "Days_Since_Password_Change",
    "Transaction_Status",
]


def _repair_list_field(value):
    """Observed, reproducible model quirk: risk_factors/mitigating_factors
    sometimes come back malformed instead of a clean JSON array of strings.
    Two distinct shapes seen so far, both handled here:
      1. A STRING containing literal '<parameter name="...">[...]' text
         instead of a real array.
      2. A string containing '<item>...</item>'-wrapped entries that are
         not valid JSON on their own (e.g. unquoted, comma-separated).
    Recover the actual list of factor strings if at all possible rather
    than silently iterating a string character-by-character, or leaving
    literal tag markup in a delivered case file."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        # shape 2: <item>...</item> wrapped entries
        item_matches = re.findall(r"<item>(.*?)</item>", value, re.DOTALL)
        if item_matches:
            return [m.strip() for m in item_matches if m.strip()]
        # shape 1: a bracketed JSON array embedded in surrounding text
        match = re.search(r"\[.*\]", value, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        return [value] if value.strip() else []
    return []


def _clean_explanation(value):
    """Observed, reproducible model quirk (same family as _repair_list_field's
    stray-tag issue): the explanation string sometimes comes back with a
    trailing '</explanation>' (or similar) closing tag leaking into the
    free text, e.g. '...moderate-high confidence.</explanation>'. Strip any
    stray XML-ish tag fragments rather than delivering them verbatim in a
    case file."""
    if not isinstance(value, str):
        return value
    return re.sub(r"</?[a-zA-Z_][\w:-]*>\s*", "", value).strip()


def _is_valid_factor_list(items):
    """Sanity check for a repaired risk_factors/mitigating_factors list:
    each entry should be a real explanatory phrase, not a stray field
    value the model grabbed by mistake (observed once: risk_factors came
    back as ["3", "8", "12", "16"] -- literal hours_seen_before values,
    not risk factors at all). Empty list is valid (mitigating_factors can
    legitimately be empty); a non-empty list where entries look like bare
    numbers/tokens instead of sentences is not."""
    if not items:
        return True
    for item in items:
        if not isinstance(item, str):
            return False
        text = item.strip()
        if len(text) < 8 or text.replace(".", "").replace("-", "").isdigit():
            return False
    return True


def _dispatch_tool(name, txn):
    if name == "lookup_user_history":
        return lookup_user_history(txn["User_ID"], txn["Transaction_ID"])
    if name == "device_intel_lookup":
        return device_intel_lookup(txn["Device_ID"])
    if name == "email_risk_lookup":
        return email_risk_lookup(txn["Email"])
    if name == "address_distance_lookup":
        return address_distance_lookup(txn.get("Billing_Location"), txn.get("Shipping_Location"))
    raise ValueError(f"Unknown tool: {name}")


def _build_system_prompt(txn):
    raw_evidence = {k: txn.get(k) for k in RAW_EVIDENCE_FIELDS}

    # get_risk_score and policy_lookup are no longer tool calls -- both are
    # already computed by routing.py before this case ever reaches the
    # agent (that's WHY the case is here at all), so making the agent call
    # them again as tools would just be re-deriving something already known,
    # adding trace noise without adding evidence. Folded into context
    # instead; the agent's tool-calling judgment is now demonstrated only
    # by the tools that actually resolve new ambiguity (lookup_user_history,
    # device_intel_lookup, email_risk_lookup).
    risk_result = get_risk_score(txn)
    policy_result = policy_lookup(txn["Transaction_Amount"])

    return (
        "You are a fraud-review agent for a PayPal-style digital wallet. You are reviewing ONE "
        "transaction that has already been routed to you because it is ambiguous (an automated "
        "system handles the clear-cut cases without you). Decide APPROVE, REJECT, or ESCALATE, "
        "with a confidence score and a plain-language explanation a human reviewer or the customer "
        "could actually check.\n\n"
        "You already have the full raw transaction record below, plus the two pieces of context "
        "that got this case routed to you in the first place -- cite any of this directly wherever "
        "relevant, you do not need a tool call for it. Your tools exist only for evidence NOT already "
        "available: this user's own prior history, a billing/shipping distance check, and two external "
        "third-party lookups (device reputation, email risk). A good analyst does not call every tool on "
        "every case -- use judgment about which tools could actually change your answer on THIS case, and "
        "only call those. Specifically:\n"
        "- lookup_user_history: skip it if Account_Age_Days is under 30 -- an account that young has no "
        "meaningful behavioral baseline yet, so the lookup would come back uninformative regardless of what "
        "it returns. Call it when the account is more established and a comparison to its own past behavior "
        "could actually confirm or contradict a suspicion (especially for anything that looks like account "
        "takeover). Do this one FIRST when you're going to call it -- its result (is_current_device_known) "
        "should directly inform whether device_intel_lookup is worth calling at all (see below).\n"
        "- address_distance_lookup: call it whenever Transaction_Type is 'Pay for Purchase' AND "
        "Billing_Location/Shipping_Location are both present on the record below -- this covers any "
        "genuinely physically-shipped purchase (e.g. Electronics). For every other transaction type, and "
        "for Travel or Gift Cards purchases specifically (nothing is physically shipped -- tickets/"
        "confirmations go to the account email, gift cards are bought as instant digital codes), these "
        "fields will be empty and the tool has nothing to check. When it IS applicable, call it even if "
        "Billing_Location and Shipping_Location look identical on the raw record -- a real distance "
        "check is worth the confirmation, not just eyeballing two city names.\n"
        "- device_intel_lookup: if you already called lookup_user_history and it reports "
        "is_current_device_known=True, do NOT also call device_intel_lookup -- you already know this is the "
        "account's own device, so a third-party reputation check on it adds nothing. Only call this when "
        "the device is unrecognized (or lookup_user_history wasn't called/wasn't informative) AND something "
        "else suggests account takeover.\n"
        "- email_risk_lookup: only call this when the case actually looks like a stolen funding instrument "
        "-- a newly-linked funding instrument, a high-risk purchase category, or elevated declines. If the "
        "case instead looks like account takeover (dormancy/reactivation, password change, unrecognized "
        "device/location), the account holder's own email is not what's in question -- skip this tool, it "
        "won't tell you anything about whether THIS is the genuine owner or an attacker.\n"
        "Call submit_decision exactly once, when you are done.\n\n"
        "Before EACH tool call, write one short sentence of plain-language reasoning explaining why you're "
        "calling it and what you expect it to tell you (e.g. 'This looks like suspected ATO given the "
        "dormancy and password change, so pulling past history to establish a device/location baseline.'). "
        "This narration is shown directly to a human reviewer alongside the tool's result, so make it "
        "genuinely explain your thinking in the moment -- not a restatement of the tool's name.\n\n"
        "IMPORTANT -- thin history is not a risk signal. If lookup_user_history reports thin_history=True "
        "or a very low n_prior_transactions, that means we have no reliable baseline for this account WITHIN "
        "OUR OBSERVATION WINDOW -- it does NOT mean the account is new or that its real-world history is thin "
        "(Account_Age_Days may be large; we simply didn't observe activity in this window). In that situation, "
        "device/location 'unrecognized' status is UNINFORMATIVE, not suspicious -- every device would look "
        "unrecognized for a zero-history account, including the genuine owner's own phone. Do NOT cite an "
        "unrecognized device/location as a risk factor when there is no real baseline to compare it against; "
        "instead, lean on evidence that doesn't depend on this account's own history (the raw-evidence risk "
        "signals below, third-party device/email reputation, policy rules) and say plainly that the history "
        "check was inconclusive rather than treating its absence as a red flag.\n\n"
        "IMPORTANT -- weigh each piece of evidence by what it actually predicts, not just by whether it's "
        "present or absent. A few specific traps to avoid:\n"
        "- A clean prior-fraud record is NOT reassuring on its own. It is uninformative for both account "
        "takeover and stolen-funding-instrument fraud -- those attacks specifically target/exploit accounts "
        "with no fraud history (that's what makes an account worth taking over or a payment method worth "
        "stealing). Do not cite 'no prior fraudulent transactions' as a mitigating factor for either pattern.\n"
        "- Decline count and velocity (transaction count) are DIFFERENT signals with different meanings -- "
        "don't treat them as one bucket. A DECLINE spike (multiple declined attempts in a short window) is "
        "specifically diagnostic of card-testing/stolen-instrument behavior (an attacker probing a stolen "
        "payment method against the issuer) -- do NOT cite 'no recent declines' as a mitigating factor for "
        "an account-takeover-looking case, it's simply not evidence about whether this is the genuine owner. "
        "A VELOCITY spike (several transactions in a short window, regardless of approval/decline) is NOT "
        "SFI-specific -- an account-takeover attacker can just as easily drain an account via several smaller "
        "transfers in quick succession as via one large one, so elevated velocity alone doesn't distinguish "
        "the two patterns and shouldn't be treated as ruling out ATO. Only decline count specifically should "
        "be read as an SFI-leaning signal; velocity is corroborating for either pattern.\n"
        "- A device reporting 'not jailbroken' is weak reassurance by itself -- jailbreaking is rare across "
        "devices generally, legitimate and fraudulent alike, so its absence doesn't meaningfully distinguish "
        "a genuine user from an attacker.\n"
        "- Account tenure is not inherently reassuring, especially alongside a dormancy-then-reactivation "
        "pattern -- long-standing, well-funded accounts are exactly what's worth taking over. Don't cite "
        "'long-tenured account' as mitigating in that situation.\n\n"
        "Your explanation must clearly justify the SPECIFIC decision, not just list evidence. Escalate means "
        "the evidence is genuinely mixed -- state explicitly what conflicts with what. Approve means the "
        "evidence is predominantly clean. Reject means the evidence is strong and largely uncontested.\n\n"
        f"Transaction record:\n{json.dumps(raw_evidence, indent=2, default=str)}\n\n"
        f"Why this case was routed to you (pre-computed, not a tool call):\n"
        f"Raw-evidence risk tier: {risk_result['risk_tier']} ({risk_result['points']} points) -- "
        f"signals matched: {risk_result['signals_matched'] or 'none'}\n"
        f"Policy check: {'TRIGGERED -- ' + policy_result['explanation'] + ' This is a safety net against a transaction this large being silently APPROVED without a second look -- it does NOT mean you must escalate no matter what. If your own analysis of the evidence clearly supports REJECT, reject it; do not let this policy rule soften a clear reject into a mere escalate. Use ESCALATE for this reason specifically when you would otherwise have leaned toward APPROVE and the amount alone means that should not happen silently.' if policy_result['rule_triggered'] else 'not triggered'}"
    )


MAX_DECISION_RETRIES = 2  # full re-runs if submit_decision comes back malformed


def run_decision_agent(txn, llm_client, verbose=False):
    """txn: dict of raw transaction fields (a paypal_transactions.csv row).
    llm_client: object with .create_message(system, messages, tools).
    Returns (decision_dict, trace) -- trace is a list of {tool, input, output}
    dicts recording every tool call made, for Phase 5 tracing/case-file use.

    Retries the ENTIRE run (not just the malformed field) up to
    MAX_DECISION_RETRIES times if risk_factors/mitigating_factors come back
    looking like garbage after repair (e.g. bare field values instead of
    real factor strings) -- this is model non-determinism, not a
    deterministic parsing bug, so a fresh generation is the right recovery,
    not a smarter parser."""
    last_error = None
    for attempt in range(MAX_DECISION_RETRIES + 1):
        try:
            decision, trace = _run_decision_agent_once(txn, llm_client, verbose=verbose)
        except RuntimeError as e:
            last_error = e
            if verbose:
                print(f"  attempt {attempt + 1} failed: {e}")
            continue
        if not _is_valid_factor_list(decision["risk_factors"]) or not _is_valid_factor_list(decision["mitigating_factors"]):
            last_error = RuntimeError(
                f"submit_decision returned garbage-looking factors after repair "
                f"(risk_factors={decision['risk_factors']!r}, mitigating_factors={decision['mitigating_factors']!r})"
            )
            if verbose:
                print(f"  attempt {attempt + 1} failed validation: {last_error}")
            continue
        return decision, trace
    raise RuntimeError(
        f"Agent produced malformed submit_decision output {MAX_DECISION_RETRIES + 1} times in a row. "
        f"Last error: {last_error}"
    )


def _run_decision_agent_once(txn, llm_client, verbose=False):
    system = _build_system_prompt(txn)
    messages = [{"role": "user", "content": "Review this transaction and decide."}]
    trace = []

    for _ in range(MAX_TOOL_ITERATIONS):
        response = llm_client.create_message(system=system, messages=messages, tools=TOOL_SCHEMAS)
        messages.append({"role": "assistant", "content": response["content"]})

        tool_calls = [b for b in response["content"] if b["type"] == "tool_use"]
        if not tool_calls:
            raise RuntimeError("Agent stopped without calling submit_decision")

        submit_calls = [c for c in tool_calls if c["name"] == "submit_decision"]
        if submit_calls:
            decision = submit_calls[0]["input"]
            decision["risk_factors"] = _repair_list_field(decision.get("risk_factors", []))
            decision["mitigating_factors"] = _repair_list_field(decision.get("mitigating_factors", []))
            if "explanation" in decision:
                decision["explanation"] = _clean_explanation(decision["explanation"])
            missing = [f for f in ("decision", "confidence", "risk_factors", "mitigating_factors", "explanation") if f not in decision]
            if missing or response.get("stop_reason") == "max_tokens":
                raise RuntimeError(
                    f"submit_decision came back incomplete (missing: {missing}, "
                    f"stop_reason={response.get('stop_reason')}) -- likely hit max_tokens mid-generation. "
                    f"Raise max_tokens on the client or shorten the requested output."
                )
            return decision, trace

        # Any text blocks in this same response are the model's inline reasoning
        # narrated immediately before its tool call(s) (per the system-prompt
        # instruction above) -- capture it so the case file can show WHY a tool
        # was called, not just its raw input/output. If the model calls multiple
        # tools in one turn, the same reasoning text precedes all of them; that's
        # fine, it just means the reasoning covered the whole batch.
        reasoning_text = " ".join(
            b["text"].strip() for b in response["content"] if b["type"] == "text" and b["text"].strip()
        ) or None

        tool_results = []
        for call in tool_calls:
            if verbose:
                print(f"  tool call: {call['name']}({call['input']})")
            output = _dispatch_tool(call["name"], txn)
            trace.append({"tool": call["name"], "input": call["input"], "output": output, "reasoning": reasoning_text})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call["id"],
                "content": json.dumps(output, default=str),
            })
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agent exceeded {MAX_TOOL_ITERATIONS} tool-call iterations without deciding")

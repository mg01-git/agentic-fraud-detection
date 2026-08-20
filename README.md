# Agentic Fraud Detection System

A demo project exploring a specific product question: **when and how should
an LLM agent be used in a fraud-review pipeline, versus deterministic
logic?** Not every transaction needs an LLM call -- most fraud/legitimate
traffic is cleanly separable with a simple rules-based score. The
interesting product and engineering work is in the routing decision itself:
what goes to an automated system unattended, what gets an LLM agent with
tools, and what the agent should and shouldn't do once it's in the loop.

**Live case-study gallery:** see `site/index.html` (or the hosted link, if
published) for real, story-driven examples of the system's routing and
agent decisions, with the agent's own reasoning shown alongside each one.

## The pipeline, in one paragraph

Every transaction gets a deterministic **raw-evidence risk score**
(`get_risk_score.py`) and a **policy check** (`policy_lookup.py`, e.g. a
mandatory review threshold above a dollar amount). Together these route
each transaction one of three ways (`routing.py`): auto-approve (the vast
majority of traffic -- clean, no signals), auto-reject-candidate (thick,
uncontested fraud signal -- see open design question below), or
**agent review** for the genuinely ambiguous middle. Only that last bucket
reaches the LLM agent (`decision_agent.py`), which has a small set of
tools -- account history, third-party device reputation, email risk, and a
billing/shipping distance check -- and is explicitly prompted to use
judgment about which tools could actually change its answer on a given
case, not call everything reflexively.

## What's in this repo

| File | What it does |
|---|---|
| `generate_synthetic_data.py` | Generates the synthetic transaction dataset and two fraud archetypes (account takeover, stolen funding instrument), plus ~30% "unexplained" fraud with zero transaction-time signal, deliberately capping achievable recall below 100% |
| `get_risk_score.py` | Deterministic raw-evidence scoring -- no LLM involved |
| `policy_lookup.py` | Mandatory-review policy rules (e.g. dollar-amount threshold) |
| `routing.py` | Combines score + policy into a routing decision |
| `decision_agent.py` | The LLM agent: system prompt, tool-calling judgment, decision loop |
| `lookup_user_history.py`, `device_intel_lookup.py`, `email_risk_lookup.py`, `address_distance_lookup.py` | The agent's tools |
| `case_file_formatter.py` | Renders one agent run into a readable case file (transaction, decision, tool calls with reasoning) |
| `real_llm_client.py` / `mock_llm_client.py` | Swappable LLM backends -- same interface, so the whole pipeline can be tested deterministically without API calls |
| `eval_harness.py` | Batch evaluation against ground truth |
| `judge_agent.py` | A second LLM call that audits the Decision Agent's own reasoning quality against hidden ground truth (early-stage) |

Design/process docs: `METHODOLOGY.md` (how the dataset and system evolved,
including real bugs found and fixed along the way), `FINDINGS.md`
(results), `MANSI_STORIES.md` (the story-first process used to curate the
demo case set -- a real narrative dictated first, then matched against or
used to identify gaps in the actual data), `POLICY.md`, `SCHEMA.md`.

## A deliberately honest design choice: ground-truth isolation

The fraud label and archetype (`Fraudulent`, `True_Archetype`,
`True_Fraud_Probability`) live in a separate file
(`ground_truth_HIDDEN.csv`), not in the main transaction data the agent
reads from. This isn't because the agent could technically read the file
directly -- it has no filesystem access, only whatever's explicitly
serialized into its prompt -- but as a structural safeguard against a
future accidental code change (e.g. a debug dump) leaking the label into
what the agent sees. Every real consumer of the label in this codebase
merges it in from the hidden file rather than reading it off the main
dataset.

## An open design question, left open on purpose

Should some transactions be **auto-rejected** at the routing layer,
skipping the agent entirely (same zero-cost profile as auto-approve),
separate from the agent independently outputting "reject" after being
invoked (which still costs a real LLM call)? The demo case set includes an
example built specifically to test this question -- an account-takeover
case with a thick, uncontested evidence pattern -- without presupposing
the answer.

## Notes on the data

All data is synthetic, generated specifically for this project (see
`METHODOLOGY.md` for why a downloaded real-looking dataset was rejected
first, and what was wrong with it). No real user data of any kind is
involved.

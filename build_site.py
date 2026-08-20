"""
Builds a single self-contained static HTML page (site/index.html) showcasing
the curated demo case set -- meant for external sharing (a friend, a resume
link) via GitHub Pages or similar. Pulls from the already-curated case files
and MANSI_STORIES.md; makes no API calls, so it can run without the
Anthropic key being available.
"""
import re
import html

CASES = [
    {
        "id": "T26574", "file": "case_files/T26574.md", "bucket": "Account takeover",
        "title": "Obvious ATO -- should skip the agent entirely",
        "story": "A long-tenured account goes dormant, reactivates with a same-day password change, "
                 "then sends a large sum to a brand-new recipient at midnight from an unrecognized device. "
                 "The evidence pattern is thick enough to ask: should this even need an LLM call?",
    },
    {
        "id": "T51839", "file": "case_files/T51839.md", "bucket": "Account takeover",
        "title": "Looks like ATO, resolved by a trusted device",
        "story": "Same dormancy + password-change shape as an ATO case -- but the agent checks the "
                 "account's own history and finds the device and destination bank are both the "
                 "genuine owner's, so it approves instead of rejecting on surface pattern alone.",
    },
    {
        "id": "T30146", "file": "case_files/T30146.md", "bucket": "Account takeover",
        "title": "Looks like ATO, resolved by a trusted shipping address",
        "story": "Same base pattern, but this time it's a high-risk-category purchase instead of a "
                 "withdrawal. The agent confirms billing and shipping addresses actually match via a "
                 "real distance check -- not just eyeballing two city names -- before approving.",
    },
    {
        "id": "T30676", "file": "case_files/T30676.md", "bucket": "Policy",
        "title": "Clean transaction, flagged only because of the dollar amount",
        "story": "Nothing about this transaction looks risky -- it only reaches the agent because a "
                 "mandatory policy rule requires review above a dollar threshold. The agent correctly "
                 "reads the policy trigger as a safety net, not as evidence of risk.",
    },
    {
        "id": "T8942", "file": "case_files/T8942.md", "bucket": "Stolen funding instrument",
        "title": "Genuinely mixed evidence -- escalated to a human",
        "story": "A freshly-linked card and a high-risk purchase category point toward a stolen "
                 "instrument, but the device, location, and recipient all check out as the account's "
                 "own. The agent doesn't force a verdict either way -- it escalates.",
    },
    {
        "id": "T7917", "file": "case_files/T7917.md", "bucket": "Stolen funding instrument",
        "title": "Fresh card, new email, and a shipping address 1,800 miles away",
        "story": "A freshly-linked funding instrument, a very young/high-risk email despite an old "
                 "account, and a shipping address in a different region than billing -- all "
                 "independently corroborating. The agent rejects with high confidence.",
    },
    {
        "id": "T43087", "file": "case_files/T43087.md", "bucket": "Stolen funding instrument",
        "title": "Fresh card + high-risk category, resolved by an old, low-risk email",
        "story": "The raw signals look like a stolen card being tested -- but the email tied to the "
                 "purchase is over three years old with a low third-party risk rating, which the agent "
                 "weighs as real evidence the genuine owner just linked a new card.",
    },
    {
        "id": "T15996_auto_approve", "file": "case_files/T15996_auto_approve.md", "bucket": "Auto-approve",
        "title": "The boring, correct case",
        "story": "No signals, nothing ambiguous -- this transaction never reaches the agent at all. "
                 "Most real traffic looks like this, and the system is designed to leave it alone.",
    },
    {
        "id": "T11117_auto_approve", "file": "case_files/T11117_auto_approve.md", "bucket": "Auto-approve",
        "title": "\"Friendly fraud\" that no system could have caught",
        "story": "An everyday-looking purchase, auto-approved -- and later disputed as fraud anyway. "
                 "There was zero transaction-time signal to find. Included deliberately, to be honest "
                 "about the system's real ceiling rather than implying total coverage.",
    },
]

DECISION_META = {
    "APPROVE": {"class": "approve", "icon": "✓", "label": "Approve"},
    "REJECT": {"class": "reject", "icon": "✕", "label": "Reject"},
    "ESCALATE": {"class": "escalate", "icon": "▲", "label": "Escalate"},
}


def parse_case(path):
    text = open(path).read()
    routed = re.search(r"\*\*Routed to agent because:\*\* `(\w+)` \(risk_tier=(\w+), risk_points=(\d+), policy_triggered=(\w+)\)", text)
    routing_decision = re.search(r"\*\*Routing decision:\*\* `(\w+)` \(risk_tier=(\w+), risk_points=(\d+)", text)
    decision_m = re.search(r"### (?:[^\s]+ )?(APPROVE|REJECT|ESCALATE) \(confidence: (\d+)%\)", text)
    ground_truth_m = re.search(r"\*\*Fraudulent:\*\* (\d)", text)
    archetype_m = re.search(r"\*\*True archetype:\*\* ([\w_]+)", text)

    def section(name, next_names):
        pat = rf"\*\*{name}:\*\*\n(.*?)(?=\n\*\*|\n## |\Z)"
        m = re.search(pat, text, re.DOTALL)
        return m.group(1).strip() if m else None

    risk_factors = re.findall(r"\*\*.warning.? Risk factors:\*\*\n(.*?)(?=\n\*\*|\n## )", text, re.DOTALL)
    mitigating = re.findall(r"\*\*.check_mark.? Mitigating factors:\*\*\n(.*?)(?=\n\*\*|\n## )", text, re.DOTALL)
    # fallback: just grab bullet lists after the headers regardless of emoji
    def bullets_after(label):
        m = re.search(rf"\*\*[^\n]*{label}[^\n]*:\*\*\n((?:- .*\n?)+)", text)
        if not m:
            return []
        return [l[2:].strip() for l in m.group(1).strip().split("\n")]

    explanation_m = re.search(r"\*\*Why this decision:\*\*\n\n(.*?)\n\n##", text, re.DOTALL)
    if not explanation_m:
        explanation_m = re.search(r"\*\*Why this decision:\*\*\n(.*?)\n\n##", text, re.DOTALL)

    txn_record_m = re.search(r"## Transaction record\n\n(.*?)\n\n##", text, re.DOTALL)
    tool_calls_m = re.search(r"## Tool calls\n\n(.*)\Z", text, re.DOTALL)

    return {
        "routed_reason": routed.group(1) if routed else (routing_decision.group(1) if routing_decision else None),
        "risk_tier": (routed or routing_decision).group(2) if (routed or routing_decision) else None,
        "risk_points": (routed or routing_decision).group(3) if (routed or routing_decision) else None,
        "decision": decision_m.group(1) if decision_m else None,
        "confidence": decision_m.group(2) if decision_m else None,
        "risk_factors": bullets_after("Risk factors"),
        "mitigating_factors": bullets_after("Mitigating factors"),
        "explanation": explanation_m.group(1).strip() if explanation_m else None,
        "txn_record": txn_record_m.group(1).strip() if txn_record_m else None,
        "tool_calls_raw": tool_calls_m.group(1).strip() if tool_calls_m else None,
        "fraudulent": ground_truth_m.group(1) if ground_truth_m else None,
        "archetype": archetype_m.group(1) if archetype_m else None,
    }


import markdown as md

def render_bullets(items):
    if not items:
        return "<p class=\"muted\">None.</p>"
    return "<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ul>"


def render_tool_calls(raw):
    if not raw:
        return ""
    return md.markdown(raw)


cards_html = []
nav_html = []
bucket_order = []
for c in CASES:
    if c["bucket"] not in bucket_order:
        bucket_order.append(c["bucket"])

for c in CASES:
    data = parse_case(f"/home/claude/fraud_project/{c['file']}")
    dm = DECISION_META.get(data["decision"], {"class": "approve", "icon": "?", "label": data["decision"] or "?"})
    nav_html.append(f'<a href="#{c["id"]}" class="navlink">{html.escape(c["title"])}</a>')

    routed_line = ""
    if data["routed_reason"]:
        routed_line = (f'<div class="routed">Routed because: <code>{html.escape(data["routed_reason"])}</code> '
                        f'&middot; raw-evidence tier: <strong>{html.escape(data["risk_tier"] or "")}</strong> '
                        f'({html.escape(str(data["risk_points"] or ""))} pts)</div>')

    gt_line = ""
    if data["fraudulent"] is not None:
        gt_label = "Confirmed fraud" if data["fraudulent"] == "1" else "Legitimate"
        gt_line = f'<div class="gt">Ground truth: <strong>{gt_label}</strong> (archetype: {html.escape(data["archetype"] or "none")})</div>'

    txn_html = ""
    if data["txn_record"]:
        txn_html = md.markdown(data["txn_record"])

    tool_html = render_tool_calls(data["tool_calls_raw"])

    card = f"""
    <section class="case" id="{c['id']}">
      <div class="case-header">
        <span class="bucket-tag">{html.escape(c['bucket'])}</span>
        <h3>{html.escape(c['title'])}</h3>
        <span class="badge {dm['class']}">{dm['icon']} {dm['label']}{' &middot; ' + data['confidence'] + '%' if data['confidence'] else ''}</span>
      </div>
      <p class="story">{html.escape(c['story'])}</p>
      {routed_line}
      {gt_line}
      <div class="grid2">
        <div>
          <h4>Risk factors</h4>
          {render_bullets(data['risk_factors'])}
          <h4>Mitigating factors</h4>
          {render_bullets(data['mitigating_factors'])}
        </div>
        <div>
          <h4>Why this decision</h4>
          <p>{html.escape(data['explanation'] or '')}</p>
        </div>
      </div>
      <details>
        <summary>Transaction record &amp; tool calls</summary>
        <div class="grid2">
          <div>{txn_html}</div>
          <div>{tool_html}</div>
        </div>
      </details>
    </section>
    """
    cards_html.append(card)

nav = "\n".join(nav_html)
cards = "\n".join(cards_html)

STYLES = """
:root {
  --surface: #ffffff; --surface-alt: #f7f8fa; --border: #e3e6ea;
  --ink-primary: #1a1d21; --ink-secondary: #4b5158; --ink-muted: #7a828c;
  --accent: #3d5afe;
  --good: #1c8a4b; --good-bg: #e8f6ee;
  --warn: #a9660a; --warn-bg: #fdf1de;
  --bad: #c22a2a; --bad-bg: #fbe9e9;
}
* { box-sizing: border-box; }
body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  color: var(--ink-primary); background: var(--surface-alt); line-height:1.55; }
.hero { background: var(--surface); border-bottom:1px solid var(--border); padding: 48px 24px 32px; }
.hero-inner, .casenav-inner, main { max-width: 920px; margin: 0 auto; }
.hero h1 { margin:0 0 12px; font-size: 2rem; }
.tagline { color: var(--ink-secondary); max-width: 760px; }
.tagline.muted { color: var(--ink-muted); font-size: 0.92rem; }
.casenav { background: var(--surface); border-bottom:1px solid var(--border); position: sticky; top:0;
  overflow-x:auto; white-space:nowrap; z-index: 10; }
.casenav-inner { padding: 10px 24px; }
.navlink { display:inline-block; margin-right:16px; font-size:0.85rem; color: var(--ink-secondary);
  text-decoration:none; }
.navlink:hover { color: var(--accent); }
main { padding: 24px; }
.case { background: var(--surface); border:1px solid var(--border); border-radius:10px; padding:24px;
  margin-bottom:20px; scroll-margin-top: 60px; }
.case-header { display:flex; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:6px; }
.case-header h3 { margin:0; font-size:1.15rem; flex:1 1 auto; }
.bucket-tag { font-size:0.72rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--ink-muted);
  border:1px solid var(--border); border-radius: 999px; padding:2px 10px; }
.badge { font-size:0.85rem; font-weight:600; border-radius:999px; padding:4px 12px; white-space:nowrap; }
.badge.approve { color: var(--good); background: var(--good-bg); }
.badge.reject { color: var(--bad); background: var(--bad-bg); }
.badge.escalate { color: var(--warn); background: var(--warn-bg); }
.story { color: var(--ink-secondary); font-style: italic; margin: 8px 0 12px; }
.routed, .gt { font-size:0.85rem; color: var(--ink-muted); margin-bottom:4px; }
.routed code { background: var(--surface-alt); padding:1px 6px; border-radius:4px; }
.grid2 { display:grid; grid-template-columns: 1fr 1fr; gap:20px; margin-top:12px; }
.grid2 h4 { margin: 12px 0 4px; font-size:0.9rem; color: var(--ink-secondary); }
.grid2 ul { margin:4px 0; padding-left:20px; font-size:0.92rem; }
.grid2 p { font-size:0.92rem; margin: 4px 0; }
details { margin-top:16px; border-top:1px solid var(--border); padding-top:12px; }
summary { cursor:pointer; font-size:0.85rem; color: var(--accent); font-weight:600; }
details .grid2 { font-size:0.85rem; }
details ul { padding-left:18px; }
footer { text-align:center; color: var(--ink-muted); font-size:0.85rem; padding: 32px 24px 48px; }
@media (max-width: 700px) { .grid2 { grid-template-columns: 1fr; } }
"""

BODY = """
<header class="hero">
  <div class="hero-inner">
    <h1>Agentic Fraud Detection System</h1>
    <p class="tagline">A demo system for reasoning about when and how to apply an LLM agent to fraud
    review -- not everything needs one. A deterministic raw-evidence score and policy layer auto-clears
    or flags the vast majority of traffic; only genuinely ambiguous cases reach an LLM agent equipped
    with a small set of tools (account history, device reputation, email risk, address distance) that
    it calls selectively, with visible reasoning for each call.</p>
    <p class="tagline muted">All data below is synthetic, generated for this project. Case studies are
    real rows run through the actual agent (not scripted) -- shown alongside the story each one was
    curated to represent.</p>
  </div>
</header>
<nav class="casenav"><div class="casenav-inner">""" + nav + """</div></nav>
<main>
""" + cards + """
</main>
<footer>
  <p>Built as a portfolio project exploring agentic AI system design and product judgment around
  generative AI vs. deterministic logic in a fraud-review context.</p>
</footer>
"""

PAGE = ("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        "<title>Agentic Fraud Detection -- Case Study Gallery</title>\n<style>\n" + STYLES + "\n</style>\n"
        "</head>\n<body>\n" + BODY + "\n</body>\n</html>")

with open("/home/claude/fraud_project/site/index.html", "w") as f:
    f.write(PAGE)

print("Wrote site/index.html")

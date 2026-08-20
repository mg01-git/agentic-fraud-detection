"""
Phase 6: eval harness.

Runs the real Decision Agent (real_llm_client -> actual Anthropic API) over
a RANDOM, stratified sample of the agent-review band -- deliberately NOT
hand-picked, unlike DEMO_SET.md -- so the resulting numbers are an honest,
defensible accuracy claim, not a curated story. See DEMO_SET.md's own note
on this distinction.

Only samples from the agent-review band (score >= 0.85 or policy-triggered)
-- the auto-approve band never reaches the agent by design (routing.py),
so there's nothing to evaluate there.

Designed to run unattended: every case is wrapped so one failure doesn't
kill the run, progress is checkpointed to disk every CHECKPOINT_EVERY
cases, and the script can be safely re-run (it will just overwrite -- see
"resuming" note at the bottom if we need that later).
"""

import json
import time
import traceback

import pandas as pd

from decision_agent import run_decision_agent
from real_llm_client import RealLLMClient
from routing import route_transaction

SAMPLE_SIZE = 90
MIN_PER_STRATUM = 3
RANDOM_SEED = 42
CHECKPOINT_EVERY = 10
RESULTS_CSV = "eval_results.csv"
POOL_CSV = "_eval_pool.csv"  # precomputed agent_review band, see build_eval_pool()


def build_eval_pool(transactions_csv="paypal_transactions.csv", ground_truth_csv="ground_truth_HIDDEN.csv"):
    """Vectorized (fast) recomputation of the agent_review band with archetype
    labels attached -- NOT via routing.route_transaction() row-by-row (that
    reloads/rescopes the model per row and is far too slow over 51k rows).
    Uses the same underlying model + features, just batched. Writes the pool
    to POOL_CSV so this only needs to run once."""
    import pickle
    from features import engineer_features_for_training

    df = pd.read_csv(transactions_csv)
    gt = pd.read_csv(ground_truth_csv)
    merged = df.merge(gt[["Transaction_ID", "True_Archetype"]], on="Transaction_ID", how="left")

    with open("risk_model.pkl", "rb") as f:
        bundle = pickle.load(f)
    model = bundle["model"]

    X, y, feat_names, profiles = engineer_features_for_training(merged)
    X = X.reindex(columns=bundle["feature_names"], fill_value=0)
    merged["risk_score"] = model.predict_proba(X)[:, 1]
    merged["policy_triggered"] = merged["Transaction_Amount"] >= 500.0
    merged["route"] = (
        (merged["risk_score"] >= 0.85) | merged["policy_triggered"]
    ).map({True: "agent_review", False: "auto_approve"})

    band = merged[merged.route == "agent_review"].copy()
    band.to_csv(POOL_CSV, index=False)
    return band


def select_eval_sample(pool_csv=POOL_CSV, n=SAMPLE_SIZE, min_per_stratum=MIN_PER_STRATUM, seed=RANDOM_SEED):
    """Stratified RANDOM sample by (True_Archetype, Fraudulent) -- proportional
    to each stratum's real size in the agent-review band, with a floor of
    min_per_stratum so small strata (e.g. 'none'/friendly-fraud at high
    score, only 9 total) aren't zeroed out by rounding. This is genuinely
    random within each stratum (pandas .sample(random_state=seed)), not
    hand-picked -- that's the whole point of this harness vs. DEMO_SET.md."""
    pool = pd.read_csv(pool_csv)
    strata = pool.groupby(["True_Archetype", "Fraudulent"])

    sizes = strata.size()
    base = sizes.clip(upper=min_per_stratum)
    remaining = n - base.sum()
    if remaining > 0:
        proportional_pool_size = (sizes - base).clip(lower=0)
        weights = proportional_pool_size / proportional_pool_size.sum()
        extra = (weights * remaining).round().astype(int)
        # rounding can over/undershoot by a few -- trim/pad against the largest stratum
        allocation = base + extra
        drift = n - allocation.sum()
        largest = allocation.idxmax()
        allocation[largest] += drift
    else:
        allocation = base

    parts = []
    for key, count in allocation.items():
        group = strata.get_group(key)
        count = min(count, len(group))
        parts.append(group.sample(n=count, random_state=seed))
    sample = pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)  # shuffle
    return sample


def score_decision(decision_label, fraudulent):
    """Rubric: fraud -> reject is ideal, escalate is acceptable (human catches
    it), approve is a real miss. Legit -> approve is ideal, escalate is
    acceptable-but-costly, reject is a real miss (blocks a real customer)."""
    if fraudulent == 1:
        return {"reject": "ideal", "escalate": "acceptable", "approve": "miss"}[decision_label]
    else:
        return {"approve": "ideal", "escalate": "acceptable", "reject": "miss"}[decision_label]


def run_eval(sample, out_csv=RESULTS_CSV, verbose=True, resume=False):
    """resume=True: skip Transaction_IDs already present in out_csv with a
    non-ERROR outcome (picks back up after an interrupted run without
    re-spending API calls on cases we already have a clean result for;
    ERROR rows ARE retried, since those failures were transient)."""
    client = RealLLMClient()
    results = []
    already_done = set()

    if resume:
        try:
            prior = pd.read_csv(out_csv)
            results = prior[prior.outcome != "ERROR"].to_dict("records")
            already_done = set(r["Transaction_ID"] for r in results)
            if verbose:
                print(f"Resuming: {len(already_done)} cases already complete, skipping those.")
        except FileNotFoundError:
            pass

    for i, row in sample.iterrows():
        txn = row.to_dict()
        tid = txn["Transaction_ID"]
        if tid in already_done:
            continue
        t0 = time.time()
        try:
            route_info = route_transaction(txn)
            decision, trace = run_decision_agent(txn, client, verbose=False)
            outcome = score_decision(decision["decision"], txn["Fraudulent"])
            row_result = {
                "Transaction_ID": tid,
                "True_Archetype": txn.get("True_Archetype"),
                "Fraudulent": txn["Fraudulent"],
                "risk_score": route_info["risk_score"],
                "route_reason": route_info["reason"],
                "agent_decision": decision["decision"],
                "agent_confidence": decision["confidence"],
                "outcome": outcome,
                "n_tool_calls": len(trace),
                "tools_called": ";".join(sorted(set(t["tool"] for t in trace))),
                "n_risk_factors": len(decision["risk_factors"]),
                "n_mitigating_factors": len(decision["mitigating_factors"]),
                "explanation": decision["explanation"],
                "error": None,
                "elapsed_sec": round(time.time() - t0, 1),
            }
        except Exception as e:
            row_result = {
                "Transaction_ID": tid,
                "True_Archetype": txn.get("True_Archetype"),
                "Fraudulent": txn["Fraudulent"],
                "risk_score": None,
                "route_reason": None,
                "agent_decision": None,
                "agent_confidence": None,
                "outcome": "ERROR",
                "n_tool_calls": None,
                "tools_called": None,
                "n_risk_factors": None,
                "n_mitigating_factors": None,
                "explanation": None,
                "error": f"{type(e).__name__}: {e}",
                "elapsed_sec": round(time.time() - t0, 1),
            }
            if verbose:
                print(f"  ERROR on {tid}: {e}")
                traceback.print_exc()

        results.append(row_result)
        if verbose:
            print(f"[{len(results)}/{len(sample)}] {tid} -> {row_result['agent_decision']} "
                  f"({row_result['outcome']}) in {row_result['elapsed_sec']}s")

        if len(results) % CHECKPOINT_EVERY == 0:
            pd.DataFrame(results).to_csv(out_csv, index=False)
            if verbose:
                print(f"  -- checkpoint saved ({len(results)} done) --")

    pd.DataFrame(results).to_csv(out_csv, index=False)
    return pd.DataFrame(results)


def summarize(results_df):
    """Aggregate stats for EVAL_RESULTS.md -- overall + by archetype."""
    clean = results_df[results_df.outcome != "ERROR"]
    summary = {
        "n_total": len(results_df),
        "n_errors": (results_df.outcome == "ERROR").sum(),
        "n_scored": len(clean),
        "outcome_counts": clean["outcome"].value_counts().to_dict(),
        "miss_rate": (clean.outcome == "miss").mean() if len(clean) else None,
        "ideal_rate": (clean.outcome == "ideal").mean() if len(clean) else None,
        "escalation_rate": (clean.outcome == "acceptable").mean() if len(clean) else None,
        "by_archetype": (
            clean.groupby(["True_Archetype", "Fraudulent"])["outcome"]
            .value_counts(normalize=True)
            .unstack(fill_value=0)
            .to_dict(orient="index")
        ),
        "avg_confidence_ideal": clean[clean.outcome == "ideal"]["agent_confidence"].mean(),
        "avg_confidence_miss": clean[clean.outcome == "miss"]["agent_confidence"].mean(),
        "avg_tool_calls": clean["n_tool_calls"].mean(),
    }
    return summary


if __name__ == "__main__":
    import sys
    resume = "--resume" in sys.argv

    if resume:
        print("Resume mode: reusing existing pool/sample selection (same seed => same 90 IDs).")
    else:
        print("Building eval pool (vectorized routing over full dataset)...")
    build_eval_pool()

    print(f"Selecting stratified random sample (n={SAMPLE_SIZE}, seed={RANDOM_SEED})...")
    sample = select_eval_sample()
    print(sample.groupby(["True_Archetype", "Fraudulent"]).size())

    print(f"\nRunning {len(sample)} cases through the real Decision Agent...")
    results = run_eval(sample, resume=resume)

    summary = summarize(results)
    with open("eval_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print("\nDone.")
    print(json.dumps(summary, indent=2, default=str))

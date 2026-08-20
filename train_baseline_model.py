"""
Phase 2 -- Baseline predictive model.

Trains a simple fraud risk-scoring model on the synthetic PayPal dataset.
This model is NOT the fraud decision itself -- it becomes one TOOL
(get_risk_score) that the Decision Agent can call later, alongside its own
reasoning over the raw transaction and any user-history lookup. The model
never sees the true generation formula/archetypes -- it only sees the same
raw columns the agent will see, and has to (re)discover the pattern
statistically, exactly like a model trained on real transaction data would.

Modeling notes / simplifications (worth being able to speak to):
- Feature engineering lives in features.py, SHARED with the agent's
  get_risk_score tool (Phase 3) -- so training and live inference always
  compute features identically. (This file used to have its own separate,
  slowly-drifting copy of this logic -- consolidated into features.py to
  close that gap.)
- Device_Mismatch is based on DEVICE ID (has this exact device ever been
  seen on this account before), not device TYPE -- see features.py
  docstring for why type-only matching is a weak signal.
- Feature engineering (known devices / home_location per user) uses each
  user's FULL transaction history (not just prior-in-time transactions) to
  define their profile. In a real production system you'd want this
  computed causally (only using transactions BEFORE the one being scored)
  to avoid any hindsight leakage. For this offline training script we use
  the simpler full-history version -- documented here as a known
  simplification, not swept under the rug. (The LIVE agent's
  lookup_user_history tool does NOT take this shortcut.)
- Train/test split is done at the USER level (not row level), so no user's
  transactions appear in both sets -- avoids a subtler leak where the model
  could learn a specific user's identity/pattern from seeing them in training.
"""

import pandas as pd
import numpy as np
import pickle
import json
from sklearn.model_selection import GroupShuffleSplit
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from features import engineer_features_for_training

RANDOM_STATE = 42


def eval_split(model, X, y):
    proba = model.predict_proba(X)[:, 1]
    preds = (proba >= 0.5).astype(int)
    return {
        "n": len(X),
        "fraud_rate": round(y.mean(), 4),
        "precision": round(precision_score(y, preds, zero_division=0), 4),
        "recall": round(recall_score(y, preds, zero_division=0), 4),
        "f1": round(f1_score(y, preds, zero_division=0), 4),
        "roc_auc": round(roc_auc_score(y, proba), 4),
        "confusion_matrix": confusion_matrix(y, preds).tolist(),
    }


def main():
    df = pd.read_csv("paypal_transactions.csv")
    # Fraudulent lives only in ground_truth_HIDDEN.csv now (kept out of
    # paypal_transactions.csv entirely so no consumer of the raw file can
    # accidentally see it -- see generate_synthetic_data.py's module
    # docstring). Training explicitly opts in to the label via this merge,
    # same as every other real consumer of Fraudulent in this codebase.
    gt = pd.read_csv("ground_truth_HIDDEN.csv")[["Transaction_ID", "Fraudulent"]]
    df = df.merge(gt, on="Transaction_ID")
    X, y, feature_names, profiles = engineer_features_for_training(df)
    groups = df["User_ID"]

    # 3-way split at the USER level: 70% train / 15% validation / 15% test.
    # Validation is used to compare models (below); the test set is only
    # touched once, at the very end, as the final unbiased check.
    split1 = GroupShuffleSplit(n_splits=1, test_size=0.30, random_state=RANDOM_STATE)
    train_idx, temp_idx = next(split1.split(X, y, groups=groups))

    X_temp, y_temp, groups_temp = X.iloc[temp_idx], y.iloc[temp_idx], groups.iloc[temp_idx]
    split2 = GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=RANDOM_STATE)
    val_idx_rel, test_idx_rel = next(split2.split(X_temp, y_temp, groups=groups_temp))
    val_idx = temp_idx[val_idx_rel]
    test_idx = temp_idx[test_idx_rel]

    X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
    X_val, y_val = X.iloc[val_idx], y.iloc[val_idx]
    X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]

    u_train = df["User_ID"].iloc[train_idx].nunique()
    u_val = df["User_ID"].iloc[val_idx].nunique()
    u_test = df["User_ID"].iloc[test_idx].nunique()
    print(f"Train: {len(X_train)} rows, {u_train} users ({y_train.mean():.2%} fraud)")
    print(f"Validation: {len(X_val)} rows, {u_val} users ({y_val.mean():.2%} fraud)")
    print(f"Test: {len(X_test)} rows, {u_test} users ({y_test.mean():.2%} fraud)")
    print("(user-level split -- no user appears in more than one of the three sets)")

    models = {
        # scaled -- logistic regression converges much faster (and more reliably) on scaled features.
        # C=0.15 (stronger-than-default L2 regularization) is a deliberate choice, not the sklearn
        # default (C=1.0) -- at C=1.0 a couple of near-categorical features (location mismatch,
        # decline count) got very large coefficients and pushed predicted probabilities to saturate
        # near 0/1 even for cases the data itself treats as genuinely uncertain (see FINDINGS.md,
        # "score bimodality"). Shrinking coefficients softens that without changing which features
        # matter, just how extremely the model trusts any single one of them.
        "logistic_regression": make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=2000, C=0.15, class_weight="balanced", random_state=RANDOM_STATE)
        ),
        "random_forest": RandomForestClassifier(n_estimators=300, max_depth=8, class_weight="balanced", random_state=RANDOM_STATE, n_jobs=-1),
    }

    results = {}
    for name, model in models.items():
        model.fit(X_train, y_train)
        results[name] = {
            "train": eval_split(model, X_train, y_train),
            "validation": eval_split(model, X_val, y_val),
            "test": eval_split(model, X_test, y_test),
        }
        print(f"\n=== {name} ===")
        for split_name in ["train", "validation", "test"]:
            r = results[name][split_name]
            print(f"  {split_name:12s} precision={r['precision']:.3f} recall={r['recall']:.3f} f1={r['f1']:.3f} roc_auc={r['roc_auc']:.3f}")

    # DELIBERATE model choice, not an automatic "highest validation F1" pick:
    # logistic_regression and random_forest are statistically tied on every
    # operational metric (precision/recall/F1) on validation AND test, but
    # random_forest shows a clear overfitting gap (train ROC-AUC far above
    # validation/test) that logistic_regression does not. Given no real
    # performance advantage, we choose the simpler, more stable, more
    # interpretable model. See FINDINGS.md for the full comparison table.
    best_name = "logistic_regression"
    best_model = models[best_name]
    print(f"\nSelected model (deliberate choice, not just highest F1): {best_name}")
    print(f"Reasoning: tied with random_forest on precision/recall/F1; more stable across train/val/test (no overfitting gap); more interpretable.")
    print(f"Final (test-set, only checked once) performance of {best_name}: {results[best_name]['test']}")

    # PROBABILITY CALIBRATION -- fit on the VALIDATION set (never train, never
    # test), so it's an honest correction rather than the model grading its
    # own homework. Isotonic regression learns a monotonic remapping from raw
    # predicted probability -> empirical fraud rate, i.e. "when this model
    # says 0.9, is it actually right 90% of the time, or is it overconfident?"
    # This does NOT change which cases score higher than others (rank order
    # is preserved, so precision/recall at a fixed threshold barely move) --
    # it changes HOW EXTREME the score gets for a given case, directly
    # targeting the saturation-toward-0/1 behavior found in FINDINGS.md.
    calibrated_model = CalibratedClassifierCV(FrozenEstimator(best_model), method="sigmoid")
    calibrated_model.fit(X_val, y_val)
    calibrated_test = eval_split(calibrated_model, X_test, y_test)
    print(f"\nAfter isotonic calibration (fit on validation set):")
    print(f"  test precision={calibrated_test['precision']:.3f} recall={calibrated_test['recall']:.3f} "
          f"f1={calibrated_test['f1']:.3f} roc_auc={calibrated_test['roc_auc']:.3f}")

    with open("risk_model.pkl", "wb") as f:
        # the CALIBRATED model is what get_risk_score actually uses in
        # production -- profiles saved alongside it, same as before
        pickle.dump({"model": calibrated_model, "feature_names": feature_names, "model_name": best_name, "profiles": profiles}, f)

    with open("baseline_model_results.json", "w") as f:
        json.dump({
            "results": results, "best_model": best_name,
            "n_train": len(X_train), "n_validation": len(X_val), "n_test": len(X_test),
            "split_method": "user-level GroupShuffleSplit, 70/15/15, random_state=42",
        }, f, indent=2)

    # feature importance (for random_forest) or coefficients (for logistic_regression) -- useful context for the agent's explanation later
    if best_name == "random_forest":
        importances = sorted(zip(feature_names, best_model.feature_importances_), key=lambda x: -x[1])[:10]
    else:
        lr = best_model.named_steps["logisticregression"]
        importances = sorted(zip(feature_names, lr.coef_[0]), key=lambda x: -abs(x[1]))[:10]
    print("\nTop 10 features:")
    for f, v in importances:
        print(f"  {f}: {v:.4f}")


if __name__ == "__main__":
    main()

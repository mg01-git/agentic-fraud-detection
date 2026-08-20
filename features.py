"""
Shared feature engineering -- used by BOTH the training script (Phase 2) and
the agent's get_risk_score tool (Phase 3), so the model always sees features
built the exact same way it was trained on. Splitting this out avoids a
classic bug: a subtly different feature computation at inference time than
at training time, which silently degrades a model without throwing an error.
"""

import pandas as pd
from collections import Counter

NUMERIC_COLS = [
    "Transaction_Amount", "Hour_of_Day", "Account_Age_Days", "Days_Since_Last_Activity",
    "Previous_Fraudulent_Transactions", "Number_of_Transactions_Last_24H", "Number_of_Declined_Transactions_Last_24H",
    "Funding_Instrument_Age_Days", "Withdrawal_Destination_Bank_Age_Days",
    "Device_Mismatch", "Location_Mismatch", "Is_New_Recipient_Flag", "Is_New_Recipient_NA",
    "Transaction_Status_Declined", "Funding_Instrument_Age_Days_Missing", "Withdrawal_Destination_Bank_Age_Days_Missing",
    "Pct_Balance_Withdrawn",
]
CATEGORICAL_COLS = ["Transaction_Type", "Purchase_Category", "Funding_Source"]


def compute_user_profiles(df):
    """One-time, offline computation of each user's 'known' devices and typical
    location from their full transaction history. Returns
    {user_id: {"known_device_ids": set(...), "home_location":...}}.

    Device_Mismatch is computed from DEVICE ID, not device TYPE (Mobile/
    Desktop/Tablet) -- a type-only comparison is a weak/noisy signal, since
    real users routinely and legitimately switch between their own devices
    of different types. A device ID never seen elsewhere in this user's
    history IS a meaningful signal (an unrecognized device on the account).
    A device ID is treated as "known" if it appears more than once across
    the user's history -- IDs that appear exactly once are, by construction,
    one-off/unrecognized (this matches how the generator creates a fresh,
    never-reused device ID for every account-takeover row).

    (Known simplification: uses full history, not strictly prior-in-time
    only -- see train_baseline_model.py docstring. The live agent's
    lookup_user_history tool does NOT take this shortcut -- it only looks
    at transactions strictly before the one being evaluated.)
    """
    home_location = df.groupby("User_ID")["IP_Location"].agg(lambda s: s.mode().iloc[0])
    profiles = {}
    for uid, group in df.groupby("User_ID"):
        device_counts = Counter(group["Device_ID"])
        known_ids = {dev_id for dev_id, count in device_counts.items() if count > 1}
        profiles[str(uid)] = {
            "known_device_ids": known_ids,
            "home_location": home_location.get(uid, "Unknown"),
        }
    return profiles


def _row_to_features_row(txn, profile):
    """txn: dict of raw transaction fields (same schema as a CSV row).
    profile: {"known_device_ids": set(...), "home_location":...} for this
    user (or a sensible default if the user has no known profile -- e.g. a
    brand-new user we've genuinely never seen, in which case we can't yet
    say any device is "unrecognized" since there's no history to compare
    against -- treated as NOT a mismatch, since flagging every first-ever
    transaction would be a trivial, useless signal)."""
    row = {}
    known_ids = profile.get("known_device_ids", set())
    device_id = txn.get("Device_ID")
    row["Device_Mismatch"] = int(bool(known_ids) and device_id not in known_ids)
    row["Location_Mismatch"] = int(txn.get("IP_Location") != profile["home_location"])

    for col in ["Funding_Instrument_Age_Days", "Withdrawal_Destination_Bank_Age_Days"]:
        val = txn.get(col, "")
        missing = (val is None) or (val == "") or (pd.isna(val) if not isinstance(val, str) else False)
        row[f"{col}_Missing"] = int(missing)
        try:
            row[col] = float(val) if not missing else -1.0
        except (TypeError, ValueError):
            row[col] = -1.0

    row["Is_New_Recipient_Flag"] = int(str(txn.get("Is_New_Recipient", "")) == "True")
    row["Is_New_Recipient_NA"] = int(str(txn.get("Is_New_Recipient", "")) == "")
    row["Transaction_Status_Declined"] = int(txn.get("Transaction_Status") == "Declined")

    for col in ["Transaction_Amount", "Hour_of_Day", "Account_Age_Days", "Days_Since_Last_Activity",
                "Previous_Fraudulent_Transactions", "Number_of_Transactions_Last_24H",
                "Number_of_Declined_Transactions_Last_24H"]:
        row[col] = float(txn.get(col, 0) or 0)

    # ratio of this transaction's amount to the account's balance snapshot --
    # the real signal behind account_takeover's "draining behavior" (see
    # generate_synthetic_data.py). Guards against a missing/zero balance
    # (shouldn't happen in generated data, but a real production feed could
    # have gaps) by falling back to 0 rather than dividing by zero.
    balance = float(txn.get("Account_Balance_Before_Transaction", 0) or 0)
    amount = float(txn.get("Transaction_Amount", 0) or 0)
    row["Pct_Balance_Withdrawn"] = round(amount / balance, 4) if balance > 0 else 0.0

    for col in CATEGORICAL_COLS:
        row[col] = txn.get(col, "NA") or "NA"

    return row


def engineer_features_for_training(df):
    """Used by train_baseline_model.py -- vectorized, whole-dataframe version."""
    profiles = compute_user_profiles(df)
    rows = []
    for _, r in df.iterrows():
        txn = r.to_dict()
        profile = profiles.get(str(txn["User_ID"]), {"known_device_ids": set(), "home_location": "Unknown"})
        rows.append(_row_to_features_row(txn, profile))
    feat_df = pd.DataFrame(rows)
    dummies = pd.get_dummies(feat_df[CATEGORICAL_COLS].fillna("NA"), prefix=CATEGORICAL_COLS)
    X = pd.concat([feat_df[NUMERIC_COLS].reset_index(drop=True), dummies.reset_index(drop=True)], axis=1)
    return X, df["Fraudulent"].astype(int), list(X.columns), profiles


def engineer_features_for_one(txn, profile, feature_names):
    """Used at inference time (the agent's get_risk_score tool) -- builds a
    single-row feature vector aligned EXACTLY to the training feature columns
    (feature_names), filling any one-hot column not triggered by this row's
    category with 0."""
    feat_row = _row_to_features_row(txn, profile)
    dummy_row = {}
    for col in CATEGORICAL_COLS:
        val = feat_row[col]
        dummy_col_name = f"{col}_{val}"
        dummy_row[dummy_col_name] = 1

    out = {}
    for name in feature_names:
        if name in NUMERIC_COLS:
            out[name] = feat_row.get(name, 0)
        else:
            out[name] = dummy_row.get(name, 0)
    return pd.DataFrame([out])[feature_names]

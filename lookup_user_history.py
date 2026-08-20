"""
Phase 3 tool: lookup_user_history.

Live, on-demand query against paypal_transactions.csv, plus one static
lookup against user_trusted_baseline.csv for TRUE identity. Not two
tools -- one tool, two sources, because they answer two different
questions:

1. "Does this transaction match who this user really is?" -- answered
   from user_trusted_baseline.csv, exported directly from the same
   generation-time user objects as the transactions themselves (see
   generate_synthetic_data.py's make_user()), so it can never conflict
   with the transaction data. This is the user's TRUE device list and
   home location as established at account creation -- not reconstructed
   from window activity. A genuinely old, established account with only
   a couple of transactions in our 180-day window still gets an honest
   known/unknown answer, because tenure/trust no longer depends on how
   much window activity happened to be generated for them.

2. "What has this user actually been doing lately?" -- answered from a
   strictly time-respecting scan of paypal_transactions.csv: only looks
   at this user's transactions that happened strictly BEFORE the one
   being evaluated (Day_Number, then Hour_of_Day, then Transaction_ID as
   a final tiebreak), because a live production system cannot see the
   future. Typical amount, recipients, funding source, hours-of-day
   pattern, and live 24h count/decline-rate all come from this real,
   causal window scan -- these are genuinely time-series stats, not
   identity, so they still need live computation even though identity no
   longer does.

AN EARLIER VERSION of this tool defined "known device" by TENURE within
the transaction window (first seen >= 30 days before the current
transaction, reconstructed from window activity) rather than from a true
baseline. That measured "how much window history happened to exist for
this account," not "is this really the user's device" -- a real
long-established account with sparse window activity could read as "no
known device" simply because the window didn't happen to contain an
old-enough sighting. Replaced by the true-baseline lookup above, which
fixes this at the source instead of patching the window-based logic.

Returns an explicit THIN_HISTORY flag (too few transactions and/or too
short a time span observed IN THE WINDOW) rather than silently reporting
a behavioral baseline (typical amount, recipients, hours-of-day pattern)
built from almost nothing -- the agent should treat any behavioral
match/mismatch claim skeptically on a thin-history account. This is now
fully decoupled from device/location trust (which comes from the true
baseline, not the window) -- a thin-window account can still have a
confidently known device, and a thick-window account's behavioral stats
are still just window stats.
"""

import pandas as pd

THIN_HISTORY_MIN_TXNS = 5        # fewer prior transactions than this -> thin history
THIN_HISTORY_MIN_DAYS_SPAN = 14  # less observed time span than this -> thin history

_df_cache = None
_baseline_cache = None


def _load_df():
    global _df_cache
    if _df_cache is None:
        _df_cache = pd.read_csv("paypal_transactions.csv")
    return _df_cache


def _load_baseline():
    """user_trusted_baseline.csv -- the user's TRUE identity as established
    at account creation (see generate_synthetic_data.py), NOT reconstructed
    from window activity. This is what fixes the old bug where "known
    device" depended on having enough observed transactions in our 180-day
    window -- a genuinely new account with zero window history still has a
    well-defined true baseline here."""
    global _baseline_cache
    if _baseline_cache is None:
        base = pd.read_csv("user_trusted_baseline.csv")
        _baseline_cache = {
            int(row["User_ID"]): {
                "trusted_device_ids": set(row["Trusted_Device_IDs"].split(";")) if row["Trusted_Device_IDs"] else set(),
                "home_location": row["Home_Location"],
                "typical_amount": float(row["Typical_Transaction_Amount"]),
                "lifetime_txns_before_window": int(row["Lifetime_Transactions_Before_Window"]),
            }
            for _, row in base.iterrows()
        }
    return _baseline_cache


def _baseline_typical_amount(user_id):
    """Fallback typical-amount source for accounts with zero prior window
    transactions -- reads the true baseline median set at account creation
    (see generate_synthetic_data.py) instead of returning None."""
    entry = _load_baseline().get(user_id)
    return entry["typical_amount"] if entry else None


def _txn_num(txn_id):
    return int(str(txn_id).lstrip("T"))


def lookup_user_history(user_id, transaction_id, df=None):
    """user_id, transaction_id identify the transaction being evaluated.
    df: optional pre-loaded DataFrame (paypal_transactions.csv); loaded
    once and cached if not provided."""
    df = df if df is not None else _load_df()

    current = df[df["Transaction_ID"] == transaction_id]
    if current.empty:
        raise ValueError(f"Transaction_ID {transaction_id} not found")
    current = current.iloc[0]
    current_day = int(current["Day_Number"])
    current_hour = int(current["Hour_of_Day"])
    current_key = (current_day, current_hour, _txn_num(transaction_id))

    user_rows = df[df["User_ID"] == current["User_ID"]].copy()
    user_rows["_key"] = list(zip(
        user_rows["Day_Number"].astype(int),
        user_rows["Hour_of_Day"].astype(int),
        user_rows["Transaction_ID"].map(_txn_num),
    ))
    prior = user_rows[user_rows["_key"] < current_key].sort_values("_key")

    n_prior = len(prior)
    days_span = (current_day - int(prior["Day_Number"].min())) if n_prior else 0
    thin_history = (n_prior < THIN_HISTORY_MIN_TXNS) or (days_span < THIN_HISTORY_MIN_DAYS_SPAN)

    # Device/location trust now comes from the TRUE baseline (account's real
    # identity at creation), not reconstructed from window activity -- fixes
    # the old bug where a genuinely old account with sparse window activity
    # read as "no known device" even though a real baseline existed. This
    # means thin_history and device/location trust are now fully decoupled:
    # a brand-new-to-our-window but real, established account still gets an
    # honest known/unknown answer, not a forced "uninformative."
    baseline = _load_baseline().get(int(current["User_ID"]), {
        "trusted_device_ids": set(), "home_location": None, "lifetime_txns_before_window": 0,
    })
    trusted_devices = baseline["trusted_device_ids"]
    trusted_locations = {baseline["home_location"]} if baseline["home_location"] else set()

    is_device_known = current["Device_ID"] in trusted_devices
    is_location_known = current["IP_Location"] in trusted_locations

    # ESTIMATED lifetime transaction count -- baseline count (transactions
    # this account did before our 180-day window even started, set at
    # account creation) plus however many window transactions came before
    # this one. Deliberately kept SEPARATE from n_prior_transactions below:
    # n_prior_transactions is the real, actually-counted number of rows we
    # can inspect the details of (needed to trust typical_amount/recipients/
    # etc.); this is an honest total count for tenure/narrative purposes
    # only -- there's no real row behind the pre-window portion, so no
    # amount/recipient/device detail exists for it.
    estimated_lifetime_txns = baseline["lifetime_txns_before_window"] + n_prior

    # typical amount still prefers real observed history (more precise, this
    # user's actual recent behavior) but falls back to the true baseline
    # median when window history is too thin to compute one at all -- so a
    # thin-window-but-real account still gets a real typical-amount answer
    # instead of None.
    if n_prior:
        typical_amount = float(prior["Transaction_Amount"].median())
    else:
        typical_amount = _baseline_typical_amount(int(current["User_ID"]))

    recipient_rows = prior[prior["Transaction_Type"].isin(["Send Money", "Pay for Purchase"])]
    distinct_recipients = int(recipient_rows["Counterparty_ID"].nunique()) if len(recipient_rows) else 0

    # typical_funding_source REMOVED by request -- not a meaningful baseline
    # to check someone against (users legitimately switch funding sources
    # for all sorts of mundane reasons; "doesn't match their usual funding
    # source" isn't a real behavioral-deviation signal the way an
    # unrecognized device/location is).

    # live 24h window, computed from actual day/hour arithmetic (not a
    # generated approximation) -- this is the honest cross-check against
    # Number_of_Transactions_Last_24H / Number_of_Declined_Transactions_Last_24H
    # in the main file, which are plausible GENERATED counts, not literally
    # aggregated by counting neighboring rows (see module docstring).
    def within_24h(row):
        elapsed_hours = (current_day - row["Day_Number"]) * 24 + (current_hour - row["Hour_of_Day"])
        return 0 <= elapsed_hours <= 24

    if n_prior:
        recent = prior[prior.apply(within_24h, axis=1)]
    else:
        recent = prior
    live_txn_count_24h = len(recent)
    # a whole-number COUNT, matching Number_of_Declined_Transactions_Last_24H's
    # units exactly -- this used to be a 0-1 rate, which no longer matched the
    # main file's field after that was changed from a percentage to a count
    live_declined_count_24h = int((recent["Transaction_Status"] == "Declined").sum()) if live_txn_count_24h else 0

    if n_prior:
        last = prior.iloc[-1]
        last_txn_summary = {
            "Transaction_ID": last["Transaction_ID"],
            "Day_Number": int(last["Day_Number"]),
            "Hour_of_Day": int(last["Hour_of_Day"]),
            "Transaction_Type": last["Transaction_Type"],
            "Transaction_Amount": float(last["Transaction_Amount"]),
            "Device_ID": last["Device_ID"],
            "IP_Location": last["IP_Location"],
            "Transaction_Status": last["Transaction_Status"],
        }
    else:
        last_txn_summary = None

    return {
        "n_prior_transactions": n_prior,
        "estimated_lifetime_transaction_count": estimated_lifetime_txns,
        "observed_days_span": days_span,
        "thin_history": thin_history,
        "is_current_device_known": is_device_known,
        "trusted_device_ids": sorted(trusted_devices),
        "is_current_location_known": is_location_known,
        "trusted_locations": sorted(trusted_locations),
        "typical_transaction_amount": typical_amount,
        "distinct_known_recipients": distinct_recipients,
        "live_transaction_count_last_24h": live_txn_count_24h,
        "live_declined_count_last_24h": live_declined_count_24h,
        "most_recent_prior_transaction": last_txn_summary,
    }


if __name__ == "__main__":
    df = _load_df()
    gt = pd.read_csv("ground_truth_HIDDEN.csv")

    # sanity checks against a few real cases
    sample_ato = df.merge(gt, on="Transaction_ID").query("True_Archetype == 'account_takeover'").iloc[0]
    r = lookup_user_history(sample_ato["User_ID"], sample_ato["Transaction_ID"], df)
    print("ATO case:", sample_ato["Transaction_ID"], "-> device known:", r["is_current_device_known"],
          "| thin history:", r["thin_history"], "| n_prior:", r["n_prior_transactions"])

    sample_legit = df.merge(gt, on="Transaction_ID").query("True_Archetype == 'none' and Fraudulent == 0").iloc[10]
    r2 = lookup_user_history(sample_legit["User_ID"], sample_legit["Transaction_ID"], df)
    print("Legit case:", sample_legit["Transaction_ID"], "-> device known:", r2["is_current_device_known"],
          "| thin history:", r2["thin_history"], "| n_prior:", r2["n_prior_transactions"])

    # a burst follow-up (SFI) should NOT have its own seed/earlier-in-burst
    # transactions poison a "known device" call -- but device is the user's
    # own so it may legitimately be known already; check the ordering itself
    sfi_burst = df.merge(gt, on="Transaction_ID").query("True_Archetype == 'stolen_funding_instrument'")
    sample_sfi = sfi_burst.iloc[5]
    r3 = lookup_user_history(sample_sfi["User_ID"], sample_sfi["Transaction_ID"], df)
    print("SFI case:", sample_sfi["Transaction_ID"], "-> n_prior:", r3["n_prior_transactions"],
          "| live_24h_count:", r3["live_transaction_count_last_24h"],
          "| live_24h_declined_count:", r3["live_declined_count_last_24h"])

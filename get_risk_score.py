"""
Phase 3 tool (now a routing-only triage filter, not a trained model): get_risk_score.

REPLACED THE TRAINED LOGISTIC REGRESSION MODEL with a transparent, hand-built
points rule -- deliberate pivot, not a fallback. See FINDINGS.md for the full
story: the trained model, even after regularization and calibration, failed
the one thing a score has to do -- rank-order the population. Every band from
0.1 to 0.9 predicted probability showed roughly the same ~45-55% real fraud
rate, meaning the score wasn't separating anything within that range, just
smoothly interpolating around the base rate.

DESIGN PRINCIPLE (Mansi's idea, the important part): this function is built
ONLY from evidence already visible on the raw transaction row -- the same
fields in decision_agent.RAW_EVIDENCE_FIELDS -- and deliberately has NO
access to anything behind a tool call (trusted-device/location lookup,
device_intel_lookup, email_risk_lookup, address_distance_lookup). This isn't
a limitation to work around, it's the actual design: this function's only
job is cheap triage ("does this look clean enough to skip review"), and the
genuinely ambiguous middle is BY CONSTRUCTION exactly the set of cases where
raw evidence alone isn't enough -- resolving those is the Decision Agent's
job, using the tools this function deliberately can't see. A raw-evidence-only
filter that could already resolve the middle would mean the tools were
pointless.

Kept the function name/shape (get_risk_score(txn) -> dict) so routing.py and
the rest of the pipeline didn't need to change beyond reading `risk_tier`
instead of a numeric `risk_score`.
"""

HIGH_RISK_CATEGORIES = {"Electronics", "Gift Cards", "Travel"}
MONEY_OUT_TYPES = {"Send Money", "Withdraw to Bank"}


def _num(txn, field, default=None):
    val = txn.get(field, "")
    if val is None or val == "" or (isinstance(val, float) and val != val):  # NaN check without pandas
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _str(txn, field):
    """Same missing-value handling as _num, for string fields -- returns
    None (not "") for missing/NaN, so a missing value can never accidentally
    compare as truthy/unequal against another missing value. This is the
    fix for a real bug: pandas reads an empty CSV cell as NaN (a float),
    and bool(NaN) is True and NaN != NaN is also True in Python -- so a
    naive `bool(a) and bool(b) and a != b` check spuriously fired "true"
    on every row where BOTH fields were simply missing, not populated and
    different. Confirmed this was firing on 100% of non-Pay-for-Purchase
    rows before this fix."""
    val = txn.get(field, "")
    if val is None or val == "" or (isinstance(val, float) and val != val):
        return None
    return str(val)


def get_risk_score(txn):
    """txn: dict of raw transaction fields.
    Returns {"risk_tier": "low"|"medium"|"high", "points": int,
    "signals_matched": [...], "model_name": "raw_evidence_ruleset"}."""
    points = 0
    signals = []

    def add(pts, name):
        nonlocal points
        points += pts
        signals.append(name)

    txn_type = txn.get("Transaction_Type", "")

    # --- account_takeover-flavored raw signals ---
    dest_bank_age = _num(txn, "Withdrawal_Destination_Bank_Age_Days")
    if dest_bank_age is not None and dest_bank_age < 5:
        add(3, "newly_linked_withdrawal_destination")

    balance = _num(txn, "Account_Balance_Before_Transaction")
    amount = _num(txn, "Transaction_Amount")
    if txn_type in MONEY_OUT_TYPES and balance and amount and balance > 0:
        pct_balance = amount / balance
        if pct_balance >= 0.35:
            add(2, "large_pct_of_balance_moved")

    days_since_last = _num(txn, "Days_Since_Last_Activity")
    is_dormant = days_since_last is not None and days_since_last >= 45
    if is_dormant:
        add(1, "dormant_reactivation")

    hour = _num(txn, "Hour_of_Day")
    if hour is not None and (hour <= 5 or hour >= 22):
        add(1, "unusual_hour")

    days_since_pw = _num(txn, "Days_Since_Password_Change")
    if is_dormant and days_since_pw is not None and days_since_pw <= 3:
        add(1, "recent_password_change_after_dormancy")

    if txn_type == "Send Money" and str(txn.get("Is_New_Recipient", "")) == "True":
        add(1, "new_recipient")

    # --- stolen_funding_instrument-flavored raw signals ---
    instrument_age = _num(txn, "Funding_Instrument_Age_Days")
    fresh_instrument = instrument_age is not None and instrument_age < 15
    if fresh_instrument:
        add(2, "newly_linked_funding_instrument")

    # elevated_24h_declines signal REMOVED -- Number_of_Declined_Transactions_Last_24H
    # is no longer a raw-evidence field (see generate_synthetic_data.py). This
    # score is deliberately raw-evidence-only, so 24h decline velocity is now
    # only checkable by the agent actually calling lookup_user_history, not
    # by this pre-agent triage filter.

    high_risk_category = txn.get("Purchase_Category") in HIGH_RISK_CATEGORIES
    if high_risk_category and fresh_instrument:
        add(1, "high_risk_category_plus_fresh_instrument")

    billing = _str(txn, "Billing_Location")
    shipping = _str(txn, "Shipping_Location")
    address_mismatch = billing is not None and shipping is not None and billing != shipping
    if address_mismatch:
        add(1, "shipping_billing_mismatch")
    if address_mismatch and fresh_instrument:
        add(1, "address_mismatch_plus_fresh_instrument")

    # prior_confirmed_fraud_on_account signal REMOVED -- checked it against
    # real data and it's spurious for exactly the reason Mansi suspected:
    # within account_takeover, fraud rate is 55.6% with no prior fraud vs
    # 57.0% with prior fraud (flat); within stolen_funding_instrument, 51.4%
    # vs 50.0% (flat, if anything reversed). It only shows an apparent
    # correlation UNCONDITIONALLY (5.6% vs 9.0%) because it's confounded by
    # exposure -- a user targeted more than once simply has more transactions
    # and more chances of both past AND current fraud. Neither archetype's
    # generation logic actually reads Previous_Fraudulent_Transactions to
    # influence fraud_prob (confirmed in generate_synthetic_data.py -- it's
    # a passive counter, write-only). Once you already suspect a specific
    # pattern, this field tells you nothing.

    # Boundaries set empirically against the real data (see FINDINGS.md), not
    # guessed -- RETUNED again after fixing a real bug in the address-mismatch
    # check (it was spuriously firing on 100% of non-Pay-for-Purchase rows,
    # see _str()'s docstring), which had been diluting the higher point
    # bands. With that fixed: points 0-2 are flat (~1.8-1.9% fraud) --
    # genuinely safe. Point 3 now breaks cleanly from baseline (~12.6%) --
    # a real signal on its own, not noise, so it no longer belongs in the
    # auto-approve tier. Point 4+ jumps hard (43-75%) -- multiple
    # corroborating raw signals firing together.
    if points <= 2:
        tier = "low"
    elif points == 3:
        tier = "medium"
    else:
        tier = "high"

    return {
        "risk_tier": tier,
        "points": points,
        "signals_matched": signals,
        "model_name": "raw_evidence_ruleset",
    }

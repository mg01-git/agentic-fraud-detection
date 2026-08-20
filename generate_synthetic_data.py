"""
Synthetic PayPal-style fraud dataset generator (v2).

IMPORTANT / DESIGN NOTE:
This script is the ONLY place the true fraud-generating logic lives. The
resulting dataset (paypal_transactions.csv) contains ONLY raw transaction
fields -- no column reveals which archetype (if any) generated a row, what
the underlying fraud probability was, or whether the transaction was
actually fraudulent. ALL of that -- Fraudulent, True_Archetype,
True_Fraud_Probability, True_Contributing_Factors -- is written separately
to ground_truth_HIDDEN.csv, for our own evaluation use only, and must
never be shown to, or used as input by, the decision agent.

This is a hard file-level boundary, not a "remember to filter this field"
convention: the agent has no filesystem access at all (see
decision_agent.py), so it can only ever see whatever fields our own code
explicitly copies into a prompt or tool result. But every downstream
consumer -- lookup_user_history, get_risk_score, case_file_formatter, any
future tool -- loads paypal_transactions.csv wholesale into memory, so if
the label lived in that file, one careless future change (a debug dump, a
new tool that forwards the raw row) could leak it with nothing to catch
it. Keeping the label physically out of the file the live pipeline reads
means that mistake is structurally impossible, not just discouraged.
Every real consumer of Fraudulent (eval_harness.py, and the __main__
sanity blocks in routing.py/policy_lookup.py/lookup_user_history.py)
already gets it by explicitly merging in ground_truth_HIDDEN.csv, which
is the one place in this codebase where opting in to seeing the label is
intentional and visible in the code, not an accident of file layout. This
also keeps label generation and agent reasoning fully independent (no
circularity): the agent has to find/reason about patterns in the raw data
itself, exactly as it would with real transaction data.

Domain: PayPal-style digital wallet, one account per user.

FRAUD LABEL SEMANTICS: Fraudulent=1 means CONFIRMED fraud (via customer
dispute/chargeback investigation, or proactive fraud-team confirmation) --
not merely suspected. This applies whether the transaction was ultimately
Approved or Declined by the issuer: a declined fraud attempt is still a
fraud attempt (attempted fraud that got blocked), consistent with how a
real fraud-ops team tracks both successful and blocked fraud.

Known, deliberate simplification (documented, not fixed): fraud history
(Previous_Fraudulent_Transactions) is tracked at the ACCOUNT level only.
In production, a major additional signal would be tracking a specific
stolen instrument (card/bank account) across the ENTIRE PayPal ecosystem
-- i.e. has this exact card been used fraudulently on *other* accounts --
not just this one account's own history. Not modeled here to keep scope
contained; called out in the README as a known limitation / future work.

Two fraud archetypes:

1. account_takeover -- attacker has taken over the victim's PayPal login.
   Signals: device and IP-derived location differ from the user's normal
   pattern; money moves OUT (Send Money / Withdraw to Bank) always funded
   from PayPal Balance (draining the account directly), often to a
   brand-new recipient or a newly-linked destination bank (low
   Withdrawal_Destination_Bank_Age_Days); amounts skew higher (draining
   behavior); can happen at unusual hours; and -- importantly -- is
   disproportionately likely on accounts that had been DORMANT (a long
   gap since the user's last activity, Days_Since_Last_Activity), since
   dormant accounts are attractive, less-watched targets.

2. stolen_funding_instrument -- the account itself is the legitimate
   user's, but the transaction is funded/attempted via a linked bank
   account or card that is not the user's normal funding source, and
   which was linked very recently (low Funding_Instrument_Age_Days) --
   i.e. a stolen instrument was just added. This archetype models classic
   "card testing": a burst of several rapid attempts (high
   Number_of_Transactions_Last_24H, high Decline_Rate_Last_24H) on
   high-resale-value purchase categories (Electronics, Gift Cards,
   Travel), most of which get Declined by the issuer, occasionally one
   getting Approved. PARTIALLY correlated (not required) with the
   account's email being new/high-risk (Email_Age_Days < 90) -- this
   makes the email-risk lookup tool a real signal for this archetype and
   deliberately carves out a genuine gray zone (SFI's other signals
   otherwise fire as an all-or-nothing bundle with no ambiguous middle).

Neither archetype makes the label perfectly separable -- there's noise on
top, and a baseline low rate of "unexplained" fraud, plus a baseline low
decline rate even for entirely legitimate transactions (real declines
happen for mundane reasons too -- insufficient funds, expired card, etc.)
so decline alone is not a give-away fraud signal.

A NOTE ON "UNEXPLAINED" FRAUD (True_Archetype = "none", Fraudulent = 1):
about 30% of confirmed fraud rows don't match either archetype above --
this deliberately models "friendly fraud" (a customer falsely disputes a
transaction that was, at the time it happened, completely normal-looking
-- no device/location/velocity/instrument anomaly to find). It's real
fraud (confirmed, so it's in the numerator), but by construction there is
zero transaction-time signal to learn from. This caps achievable recall
below 100% ON PURPOSE, and is why the Decision Agent's job includes
explaining WHY a case doesn't fit a known pattern, not just scoring it.
"""

import csv
import random

random.seed(42)  # reproducible for this project; not used by the agent

N_USERS = 4000
AVG_TXNS_PER_USER = 12.5
TARGET_TXNS = int(N_USERS * AVG_TXNS_PER_USER)  # ~50,000
WINDOW_DAYS = 180  # our observation window

DEVICES = ["Mobile", "Desktop", "Tablet"]
LOCATIONS = [
    "New York", "San Francisco", "Chicago", "Los Angeles", "Houston",
    "Seattle", "Boston", "Miami", "Austin",
]
# region buckets for the billing/shipping distance tool -- coarse, real US
# geography, used only to bucket "same city / same region / cross-country",
# not literal lat-long math (see address_distance_lookup.py)
LOCATION_REGION = {
    "New York": "Northeast", "Boston": "Northeast",
    "Miami": "Southeast",
    "Houston": "South", "Austin": "South",
    "Chicago": "Midwest",
    "San Francisco": "West", "Los Angeles": "West", "Seattle": "West",
}
TXN_TYPES = ["Send Money", "Pay for Purchase", "Withdraw to Bank", "Receive Money"]
TXN_TYPE_WEIGHTS = [0.30, 0.30, 0.15, 0.25]
FUNDING_SOURCES = ["PayPal Balance", "Linked Bank Account", "Linked Debit Card", "Linked Credit Card"]
FUNDING_WEIGHTS_NORMAL = [0.55, 0.20, 0.15, 0.10]

PURCHASE_CATEGORIES = ["Electronics", "Gift Cards", "Travel", "Groceries", "Subscriptions", "Retail/Other"]
PURCHASE_CATEGORY_WEIGHTS_NORMAL = [0.12, 0.08, 0.08, 0.27, 0.20, 0.25]
HIGH_RISK_CATEGORIES = ["Electronics", "Gift Cards", "Travel"]

ARCHETYPE_ATTEMPT_RATE = 0.065        # baseline share of occasions considered for a fraud attempt
DORMANCY_THRESHOLD_DAYS = 45          # gap since last activity considered "dormant"
ARCHETYPE_WEIGHTS = {"account_takeover": 0.5, "stolen_funding_instrument": 0.5}
BASE_FRAUD_PROB = 0.008               # tiny baseline chance even with no archetype match (noise fraud)
BASE_DECLINE_PROB = 0.04              # legitimate transactions decline sometimes too (insufficient funds, etc.)


_device_id_counter = [0]
_email_id_counter = [0]

# Reference-table bookkeeping, kept OUTSIDE paypal_transactions.csv on purpose
# -- these represent costly third-party lookups the agent will call
# selectively (device_threat_intel.csv, email_risk_data.csv), not columns
# baked into the main transaction file.
_device_registry = {}   # device_id -> "known" (a user's own device) or "attacker" (account_takeover's fresh device)
EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "icloud.com", "hotmail.com"]


def gen_device_id():
    _device_id_counter[0] += 1
    return f"DEV-{_device_id_counter[0]:06d}"


def gen_email():
    _email_id_counter[0] += 1
    return f"user{_email_id_counter[0]}{random.randint(100, 999)}@{random.choice(EMAIL_DOMAINS)}"


def device_threat_rating(is_jailbroken):
    if is_jailbroken:
        return random.choices(["Medium", "High"], weights=[0.4, 0.6])[0]
    return random.choices(["Low", "Medium"], weights=[0.92, 0.08])[0]


def min_txns_for_age(account_age_days):
    """Age-correlated floor on how few transactions a user can land with in
    our 180-day window -- fixes the account-age/window-decoupling gap found
    during real-agent review (a 1,700-day-old account with a single
    transaction ever strained belief). Layered ON TOP of the existing random
    variation (see main()), not replacing it, so naturally low-activity
    long-time users can still occur, just not as the ONLY possible outcome
    for an old account."""
    if account_age_days < 90:
        return 1
    elif account_age_days < 365:
        return 3
    else:
        return 6


def make_user(user_id):
    # each user owns a small set of REAL devices (id + type) -- e.g. a phone
    # and a laptop. Using a device type they own is normal, no matter which
    # one; using a device ID never seen on this account before is the actual
    # signal (device-TYPE mismatch alone is a weak/noisy proxy, since real
    # users routinely switch between their own phone/desktop/tablet).
    n_devices = random.choices([1, 2, 3], weights=[0.5, 0.35, 0.15])[0]
    known_devices = [{"device_id": gen_device_id(), "device_type": random.choice(DEVICES)} for _ in range(n_devices)]
    for d in known_devices:
        # low baseline jailbroken rate -- legit users occasionally have a
        # jailbroken device for non-fraud reasons, so this isn't a tell on its own
        jb = random.random() < 0.04
        _device_registry[d["device_id"]] = {"category": "known", "jailbroken": jb, "threat_rating": device_threat_rating(jb)}

    # Email: most users have an established email (older than the account
    # tends to be, or close to it); a minority (~20%) have a fairly new
    # email. This is set ONCE per user at account setup, not per-transaction
    # -- it's a property of the account, like Account_Age_Days.
    if random.random() < 0.20:
        email_age_days = random.randint(1, 200)   # newer email
    else:
        email_age_days = random.randint(200, 3000)  # established email

    return {
        "user_id": user_id,
        "known_devices": known_devices,
        "email": gen_email(),
        "email_age_days": email_age_days,
        "home_location": random.choice(LOCATIONS),
        "typical_amount_mu": random.uniform(2.5, 5.5),
        "default_funding": random.choices(FUNDING_SOURCES, weights=FUNDING_WEIGHTS_NORMAL)[0],
        # GRAY-ZONE FIX: previously a hard uniform(30, 1400) -- meant NO
        # legitimate transaction ever had a funding instrument between 3 and
        # 30 days old, while stolen_funding_instrument forced 0-3 days. That
        # created a literal empty gap in the data (a real cliff, not just a
        # modeling artifact). ~15% of users now have a genuinely recently-
        # linked-but-legitimate instrument (people really do add a new card
        # and use it within days -- new bank, reissued card, etc.), so the
        # SFI archetype's own range (widened below) now has real overlap
        # with legitimate behavior instead of a hard boundary.
        "funding_instrument_established_age": (
            round(random.uniform(4, 45)) if random.random() < 0.15
            else random.randint(45, 1400)
        ),
        "destination_bank_established_age": random.randint(30, 1400),
        "known_recipients": set(),
        "prior_fraud_count": 0,
        # a stable "typical balance" for this account, loosely scaled off
        # their typical transaction size (15-80x it) -- gives a plausible
        # range from a few hundred dollars to several thousand. Each row's
        # actual balance snapshot fluctuates a bit around this (see
        # build_row) -- this is a documented simplification, NOT a
        # ledger-accurate running balance computed from every prior
        # transaction, which would require tracking cumulative in/out flow
        # per user across the whole dataset. Good enough to answer "was this
        # a small draw or most of the account," not to reconcile to the
        # penny.
        "typical_balance": round((2.71828 ** random.uniform(2.5, 5.5)) * random.uniform(15, 80), 2),
        # password last changed -- initialized at account creation; updated
        # per-occasion in build_row() when a change event fires.
        "last_password_change_day": -random.randint(0, 60),
    }


def gen_recipient_id():
    return f"U{random.randint(1, 9000)}"


def normal_amount(user):
    return round(random.lognormvariate(user["typical_amount_mu"], 0.6), 2)


def clip01(x):
    return max(0.0, min(1.0, x))


def assign_occasion_days(n, account_open_days_ago=0):
    """n sorted random days within the observation window, for one user.

    account_open_days_ago biases how EARLY the first occasion can land:
    without this, occasion days are pure uniform-random across the whole
    window, so a genuinely old account (large account_open_days_ago) could,
    purely by chance, have every one of its window transactions land late
    in the window -- meaning any transaction we evaluate for them still
    shows near-zero n_prior_transactions/observed_days_span in
    lookup_user_history, making a real long-tenured account look
    thin-history for no real reason. Forcing the first occasion to land
    early for older accounts fixes this at the source: their window
    activity now genuinely starts early, so later transactions get an
    honest, real amount of prior-window history to compare against."""
    if account_open_days_ago >= 365:
        first_day_max = 20
    elif account_open_days_ago >= 90:
        first_day_max = 60
    else:
        first_day_max = WINDOW_DAYS  # newer accounts: no forced-early bias

    first_day = random.randint(1, min(first_day_max, WINDOW_DAYS))
    if n == 1:
        return [first_day]
    rest = [random.randint(first_day, WINDOW_DAYS) for _ in range(n - 1)]
    return sorted([first_day] + rest)


def build_row(txn_id, user, day, hour, days_since_last, is_burst_followup=False,
              forced_type=None, forced_category=None, forced_status=None, forced_archetype=None):
    txn_type = forced_type or random.choices(TXN_TYPES, weights=TXN_TYPE_WEIGHTS)[0]

    # dormancy-conditioned + baseline archetype attempt decision
    is_dormant = days_since_last >= DORMANCY_THRESHOLD_DAYS
    if forced_archetype is not None:
        # burst follow-ups: same real-world event as their seed transaction,
        # so they get the SAME archetype treatment (fraud_prob/decline_prob/
        # factors), not an independent random draw -- otherwise they'd
        # silently fall back to "none" and dilute the archetype's data quality.
        archetype = forced_archetype
    else:
        attempt_rate = ARCHETYPE_ATTEMPT_RATE * (3.0 if is_dormant else 1.0)
        is_attempt = (not is_burst_followup) and random.random() < min(0.35, attempt_rate)
        archetype = None
        if is_attempt:
            # dormant accounts skew toward account_takeover specifically;
            # a newer/riskier email on the account skews toward
            # stolen_funding_instrument -- PARTIAL correlation, not a rule
            # (plenty of new-email users are never touched, plenty of SFI
            # attempts happen on old-email accounts too). This is what makes
            # the future email-risk tool a real, non-decorative signal, and
            # it also creates genuine gray-zone SFI cases, since SFI's other
            # signals otherwise fire as an all-or-nothing bundle.
            if is_dormant:
                weights = {"account_takeover": 0.75, "stolen_funding_instrument": 0.25}
            elif user["email_age_days"] < 90:
                weights = {"account_takeover": 0.30, "stolen_funding_instrument": 0.70}
            else:
                weights = ARCHETYPE_WEIGHTS
            archetype = random.choices(list(weights.keys()), weights=list(weights.values()))[0]
            if archetype == "stolen_funding_instrument" and txn_type in ("Receive Money", "Withdraw to Bank"):
                archetype = None

    chosen_device = random.choice(user["known_devices"])
    device_id = chosen_device["device_id"]
    device = chosen_device["device_type"]
    location = user["home_location"]
    amount = normal_amount(user)
    balance_before = round(max(10.0, user["typical_balance"] * random.uniform(0.85, 1.15)), 2)
    num_txns_24h = max(1, int(random.gauss(3, 2)))
    new_recipient = False
    purchase_category = ""
    funding_instrument_age = ""
    destination_bank_age = ""

    if txn_type in ("Send Money", "Pay for Purchase"):
        funding_source = user["default_funding"]
    elif txn_type == "Withdraw to Bank":
        funding_source = "PayPal Balance"
    else:
        funding_source = "N/A (Incoming)"

    if txn_type in ("Send Money", "Pay for Purchase"):
        if user["known_recipients"] and random.random() < 0.75:
            recipient_id = random.choice(list(user["known_recipients"]))
        else:
            recipient_id = gen_recipient_id()
            new_recipient = True
        counterparty_id = recipient_id
    elif txn_type == "Receive Money":
        counterparty_id = gen_recipient_id()
    else:
        counterparty_id = "SELF-BANK"

    if txn_type == "Pay for Purchase":
        purchase_category = forced_category or random.choices(
            PURCHASE_CATEGORIES, weights=PURCHASE_CATEGORY_WEIGHTS_NORMAL
        )[0]

    if funding_source in ("Linked Bank Account", "Linked Debit Card", "Linked Credit Card"):
        funding_instrument_age = user["funding_instrument_established_age"]
    if txn_type == "Withdraw to Bank":
        destination_bank_age = user["destination_bank_established_age"]

    fraud_prob = BASE_FRAUD_PROB
    decline_prob = BASE_DECLINE_PROB
    factors = []

    if archetype == "account_takeover":
        # attacker's own device -- a fresh ID never before seen on this account.
        # Its TYPE may or may not coincidentally match one of the user's own
        # devices (that's realistic and fine) -- the ID is what's never seen.
        device_id = gen_device_id()
        # elevated (not certain) jailbroken rate for the attacker's device --
        # decided now so it can PARTIALLY drive this row's fraud_prob below,
        # the same way email age partially drives stolen_funding_instrument's
        # -- otherwise the device-threat tool would be descriptive but inert.
        device_jailbroken = random.random() < 0.40
        _device_registry[device_id] = {
            "category": "attacker", "jailbroken": device_jailbroken,
            "threat_rating": device_threat_rating(device_jailbroken),
        }
        device = random.choice(DEVICES)
        location = random.choice([l for l in LOCATIONS if l != user["home_location"]])
        if txn_type not in ("Send Money", "Withdraw to Bank"):
            txn_type = random.choices(["Send Money", "Withdraw to Bank"], weights=[0.7, 0.3])[0]
        funding_source = "PayPal Balance"
        funding_instrument_age = ""
        purchase_category = ""
        # ATO amount is now tied to the account's OWN balance snapshot, not
        # just an untethered multiplier -- this is what actually makes
        # "draining behavior" a real, checkable signal (a $30 withdrawal
        # reads completely differently against a $32 balance vs. a $5,000
        # one, and the old version couldn't distinguish those).
        amount = round(balance_before * random.uniform(0.4, 0.9), 2)
        hour = random.choices(range(24), weights=[3 if (h <= 5 or h >= 22) else 1 for h in range(24)])[0]
        if txn_type == "Send Money":
            counterparty_id = gen_recipient_id()
            new_recipient = True
        else:  # Withdraw to Bank
            counterparty_id = "SELF-BANK"
            new_recipient = False
            destination_bank_age = round(random.uniform(0, 3))
        num_txns_24h = max(num_txns_24h, int(random.gauss(6, 2)))

        # GRAY-ZONE FIX: base jump lowered from 0.5 -- previously an archetype
        # hit alone guaranteed fraud_prob >= ~0.45, leaving no genuine
        # low-to-mid band for ATO. Now a bare archetype hit with no
        # corroborating factor lands around 0.30-0.40 (a real gray-zone
        # score), and only accumulates toward high confidence as multiple
        # independent signals actually co-occur -- matching how the agent is
        # supposed to reason (multiple independent signals, not one flag).
        fraud_prob += 0.35
        fraud_prob += 0.1 if new_recipient else 0
        fraud_prob += 0.1 if (hour <= 5 or hour >= 22) else 0
        fraud_prob += 0.1 if is_dormant else 0
        # partial device-risk correlation (mirrors the SFI/email-age nudge
        # above): only ADDS to the probability, doesn't define the archetype
        # -- so some jailbroken-device ATO attempts still land as legitimate
        # (noise), and non-jailbroken ones can still be genuine fraud.
        fraud_prob += 0.12 if device_jailbroken else 0.0
        decline_prob += 0.06
        factors = ["unrecognized_device_id", "ip_location_mismatch", "high_amount"]
        if hour <= 5 or hour >= 22:
            factors.append("unusual_hour")
        if is_dormant:
            factors.append("dormant_account_reactivated")
        if device_jailbroken:
            factors.append("jailbroken_device")
        if txn_type == "Withdraw to Bank":
            factors.append("newly_linked_destination_bank")
        else:
            factors.append("new_recipient")

    elif archetype == "stolen_funding_instrument":
        other_sources = [f for f in FUNDING_SOURCES if f not in (user["default_funding"], "PayPal Balance")]
        funding_source = random.choice(other_sources) if other_sources else "Linked Credit Card"
        # GRAY-ZONE FIX: widened from a hard uniform(0, 3) -- that forced a
        # literal empty gap against legit instruments (previously always
        # 30+ days). Now overlaps with the ~15% of legit users who also have
        # a recently-linked instrument (see make_user()), so instrument age
        # alone is no longer a bundled tell in the 3-15 day range.
        funding_instrument_age = round(random.uniform(0, 15))
        txn_type = "Pay for Purchase"
        purchase_category = random.choices(
            HIGH_RISK_CATEGORIES, weights=[0.45, 0.35, 0.20]
        )[0]
        amount = round(amount * random.uniform(1.3, 2.5), 2)
        num_txns_24h = max(num_txns_24h, int(random.gauss(7, 2)))

        # GRAY-ZONE FIX: base jump lowered from 0.45, same rationale as ATO
        # above -- a bare SFI hit with no corroborating factor now lands
        # around 0.25-0.35 instead of guaranteeing 0.45+.
        fraud_prob += 0.30
        fraud_prob += 0.15 if num_txns_24h >= 6 else 0
        # partial email-risk correlation (see attempt-selection above): only
        # ADDS to the probability when it happens to be true for this case --
        # doesn't define the archetype -- so SFI cases with an established
        # email don't automatically get pushed to near-certain fraud, and
        # some SFI attempts on new-email accounts still land as legitimate
        # (noise). This is what carves out a genuine gray zone for SFI.
        fraud_prob += 0.12 if user["email_age_days"] < 90 else 0.0
        decline_prob += 0.55
        factors = ["newly_linked_funding_instrument", "high_risk_purchase_category",
                    "elevated_amount", "high_decline_rate"]
        if num_txns_24h >= 6:
            factors.append("high_24h_velocity")
        if user["email_age_days"] < 90:
            factors.append("new_high_risk_email")

    # Billing/shipping address -- only meaningful for Pay for Purchase (you
    # don't ship anything for Send Money/Withdraw to Bank/Receive Money, so
    # ATO rows -- which are forced to those types -- never populate this).
    # Billing address is the account's stable address on file (home_location,
    # reused directly, not a second independent draw). Shipping mismatch is
    # a real stolen_funding_instrument cash-out signal (ship stolen goods to
    # a drop address, not your own billing address) -- deliberately also
    # fires at a low baseline rate for entirely mundane legit reasons (gift,
    # work address), so a mismatch alone is corroborating evidence, not a
    # bundled tell.
    # Travel and Gift Cards have no physical shipment -- Travel because
    # tickets/confirmations go to the account email, Gift Cards because
    # (especially in a fraud/cash-out context) they're overwhelmingly
    # bought as instant e-gift-card codes, not physical cards in the mail
    # -- a fraudster laundering a stolen instrument specifically wants the
    # instant digital code to avoid shipping risk/delay entirely, which is
    # WHY e-gift cards are the classic real-world cash-out vector. So
    # Shipping_Location is deliberately left empty for both categories even
    # though Billing_Location (the payment method's address on file) still
    # applies. This means address_distance_lookup/shipping_billing_mismatch
    # are structurally "not_applicable" for Travel/Gift Cards, same as for
    # non-Pay-for-Purchase types -- Electronics is the one HIGH_RISK
    # category that's still genuinely physically shipped.
    billing_location = ""
    shipping_location = ""
    if txn_type == "Pay for Purchase":
        billing_location = user["home_location"]
        ships_physically = purchase_category not in ("Travel", "Gift Cards")
        if ships_physically:
            if archetype == "stolen_funding_instrument":
                mismatched = random.random() < 0.55
            else:
                mismatched = random.random() < 0.08
            if mismatched:
                other_locations = [l for l in LOCATIONS if l != billing_location]
                if archetype == "stolen_funding_instrument" and random.random() < 0.70:
                    # cash-out pattern skews toward a DIFFERENT region, not just a
                    # different city -- a real drop-address tell
                    cross_region = [l for l in other_locations if LOCATION_REGION[l] != LOCATION_REGION[billing_location]]
                    shipping_location = random.choice(cross_region or other_locations)
                else:
                    shipping_location = random.choice(other_locations)
            else:
                shipping_location = billing_location

            if archetype == "stolen_funding_instrument" and mismatched:
                cross_region = LOCATION_REGION[shipping_location] != LOCATION_REGION[billing_location]
                fraud_prob += 0.15 if cross_region else 0.05
                factors.append("shipping_billing_address_mismatch")

    # Password-change gray-zone signal: a real account-takeover tell
    # (attacker locks the owner out immediately) that is ALSO something
    # legitimate users do after a long absence (self-service reset) --
    # deliberately ambiguous on its own, which is the point: it's the
    # differential PROBABILITY of occurring, not a hardcoded rule, that
    # makes this a real (not manufactured) source of the gray zone.
    # Deliberately NOT added to fraud_prob directly -- the differential
    # occurrence rate below already encodes the correlation; adding it here
    # too would just recreate another hard cliff.
    if is_dormant and archetype == "account_takeover":
        password_changed_now = random.random() < 0.65
    elif is_dormant:
        password_changed_now = random.random() < 0.30
    else:
        password_changed_now = random.random() < 0.02
    if password_changed_now:
        user["last_password_change_day"] = day
        if archetype == "account_takeover":
            factors.append("recent_password_change")
    days_since_password_change = day - user["last_password_change_day"]

    fraud_prob = clip01(fraud_prob + random.uniform(-0.05, 0.05))
    fraudulent = 1 if random.random() < fraud_prob else 0

    decline_prob = clip01(decline_prob + random.uniform(-0.03, 0.03))
    status = forced_status or ("Declined" if random.random() < decline_prob else "Approved")

    # decline COUNT over the trailing 24h window -- reported as a whole
    # number, not a percentage, so it doesn't force the reader to do mental
    # math against Number_of_Transactions_Last_24H to know what it means
    # (Mansi's feedback on T64/T2360: "17% of 8" is harder to read than "1
    # declined"). Still generated from a rate consistent with this row's own
    # risk level internally (a lightweight, documented approximation, not a
    # literal count of neighboring rows -- see README), just exposed as a
    # count.
    decline_rate_24h = clip01(decline_prob + random.uniform(-0.05, 0.1))
    num_declined_24h = min(num_txns_24h, round(decline_rate_24h * num_txns_24h))

    row = {
        "Transaction_ID": txn_id,
        "User_ID": user["user_id"],
        "Day_Number": day,
        "Hour_of_Day": hour,
        "Transaction_Type": txn_type,
        "Transaction_Amount": amount,
        "Purchase_Category": purchase_category,
        "Funding_Source": funding_source,
        "Funding_Instrument_Age_Days": funding_instrument_age,
        "Device_ID": device_id,
        "Device_Used": device,
        "Email": user["email"],
        "IP_Location": location,
        "Counterparty_ID": counterparty_id,
        "Is_New_Recipient": new_recipient if txn_type in ("Send Money", "Pay for Purchase") else "",
        "Withdrawal_Destination_Bank_Age_Days": destination_bank_age,
        "Account_Age_Days": user["account_open_days_ago"] + day,
        "Days_Since_Last_Activity": days_since_last,
        "Previous_Fraudulent_Transactions": user["prior_fraud_count"],
        # Number_of_Transactions_Last_24H / Number_of_Declined_Transactions_Last_24H
        # were REMOVED from here (num_txns_24h/num_declined_24h still exist as
        # internal variables above -- they drive fraud_prob/decline_prob for
        # the SFI card-testing-burst story, that part is unchanged). They
        # used to also be written out as a "generated, plausible" raw-evidence
        # column, deliberately never reconciled against lookup_user_history's
        # actually-counted live_transaction_count_last_24h/
        # live_declined_count_last_24h -- the two would routinely disagree
        # (e.g. row says 3 txns/1 declined in the last 24h, live count says
        # 0/0), which read as a data bug even though it was documented
        # behavior. Removed by request rather than continuing to reconcile
        # them: 24h velocity/declines now live in exactly ONE place
        # (lookup_user_history's live count), not duplicated as an
        # unreconciled raw-evidence approximation.
        "Account_Balance_Before_Transaction": balance_before,
        "Billing_Location": billing_location,
        "Shipping_Location": shipping_location,
        "Days_Since_Password_Change": days_since_password_change,
        "Transaction_Status": status,
    }
    ground_truth = {
        "Transaction_ID": txn_id,
        "Fraudulent": fraudulent,
        "True_Archetype": archetype or "none",
        "True_Fraud_Probability": round(fraud_prob, 4),
        "True_Contributing_Factors": ";".join(factors),
    }

    if txn_type in ("Send Money", "Pay for Purchase") and new_recipient:
        user["known_recipients"].add(counterparty_id)
    if fraudulent:
        user["prior_fraud_count"] += 1

    return row, ground_truth


def email_risk_rating(age_days):
    if age_days < 30:
        base = "High"
    elif age_days < 180:
        base = "Medium"
    else:
        base = "Low"
    # small amount of noise so the rating isn't a perfectly deterministic
    # function of age -- a rating service in the real world wouldn't be either
    if random.random() < 0.10:
        order = ["Low", "Medium", "High"]
        idx = order.index(base)
        base = order[max(0, min(2, idx + random.choice([-1, 1])))]
    return base


def main():
    users = {u: make_user(u) for u in range(1, N_USERS + 1)}
    for u in users.values():
        u["account_open_days_ago"] = random.randint(30, 2000)
        # lifetime transaction count as of the START of our 180-day window --
        # NOT reconstructed from window activity, purely a function of real
        # account tenure (a modest, plausible pace: roughly one transaction
        # every 12-50 days on average pre-window). This is deliberately
        # separate from window-counted history: it answers "how many
        # transactions has this account EVER done," not "how many can we
        # actually see the details of" -- we never generated the actual rows
        # for this pre-window activity, so there's no typical amount/
        # recipients/etc. behind it, just an honest count.
        u["lifetime_txns_before_window"] = round(u["account_open_days_ago"] * random.uniform(0.02, 0.08))

    user_ids = list(users.keys())
    raw_counts = [max(1, min(30, int(random.gauss(AVG_TXNS_PER_USER, 5)))) for _ in user_ids]
    scale = TARGET_TXNS / sum(raw_counts)
    user_txn_counts = {uid: max(1, round(c * scale)) for uid, c in zip(user_ids, raw_counts)}
    # age-correlated floor -- layered on top of the random variation above,
    # not replacing it (see min_txns_for_age() docstring)
    for uid in user_ids:
        floor = min_txns_for_age(users[uid]["account_open_days_ago"])
        user_txn_counts[uid] = max(user_txn_counts[uid], floor)

    rows, ground_truth_rows = [], []
    txn_id_counter = 1

    for uid in user_ids:
        user = users[uid]
        n = user_txn_counts[uid]
        occasion_days = assign_occasion_days(n, account_open_days_ago=user["account_open_days_ago"])

        # first-transaction gap: time since unknown prior activity before our
        # observation window began. Drawn from a realistic distribution (most
        # users show a recent-ish prior gap, some show a long one) -- it is NOT
        # forced away from zero; a value of 0 is a legitimate, occasional draw
        # (e.g. this genuinely was the user's very first-ever activity).
        first_gap = round(random.expovariate(1 / 20))
        prev_day = 0 - first_gap

        for i, day in enumerate(occasion_days):
            hour = random.choices(range(24), weights=[1 if 6 <= h <= 23 else 0.3 for h in range(24)])[0]
            days_since_last = day - prev_day  # naturally >= 0; can be 0 for same-day repeat activity
            prev_day = day

            txn_id = f"T{txn_id_counter}"
            txn_id_counter += 1
            row, gt = build_row(txn_id, user, day, hour, days_since_last)
            rows.append(row)
            ground_truth_rows.append(gt)

            # card-testing burst: a few extra closely-spaced attempts same day
            if gt["True_Archetype"] == "stolen_funding_instrument":
                n_extra = random.randint(1, 3)
                for _ in range(n_extra):
                    txn_id = f"T{txn_id_counter}"
                    txn_id_counter += 1
                    # always strictly later than the seed's hour (and any earlier
                    # follow-up), so ties can never occur when ordering by day+hour
                    extra_hour = min(23, hour + 1 + random.randint(0, 2))
                    hour = extra_hour
                    erow, egt = build_row(
                        txn_id, user, day, extra_hour, 0, is_burst_followup=True,
                        forced_type="Pay for Purchase", forced_category=row["Purchase_Category"],
                        forced_archetype="stolen_funding_instrument",
                    )
                    # keep these follow-ups tied to the same burst semantics
                    erow["Funding_Source"] = row["Funding_Source"]
                    erow["Funding_Instrument_Age_Days"] = row["Funding_Instrument_Age_Days"]
                    rows.append(erow)
                    ground_truth_rows.append(egt)

    combined = list(zip(rows, ground_truth_rows))
    random.shuffle(combined)
    rows, ground_truth_rows = zip(*combined)

    fieldnames = list(rows[0].keys())
    with open("paypal_transactions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    gt_fieldnames = list(ground_truth_rows[0].keys())
    with open("ground_truth_HIDDEN.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=gt_fieldnames)
        w.writeheader()
        w.writerows(ground_truth_rows)

    # --- Reference tables: separate, deliberately NOT merged into
    # paypal_transactions.csv -- they represent external, costly third-party
    # data sources the agent queries SELECTIVELY via tools (device threat
    # intel for account_takeover cases, email risk for stolen_funding_
    # instrument cases), not free columns sitting in the main file.
    device_rows = []
    for device_id, info in _device_registry.items():
        # jailbroken/threat status was decided at device-generation time
        # (above) so it could partially drive the row's fraud_prob -- here
        # we just report what was already decided, not re-draw it.
        device_rows.append({
            "Device_ID": device_id,
            "Is_Jailbroken": info["jailbroken"],
            "Device_Threat_Rating": info["threat_rating"],
        })
    with open("device_threat_intel.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Device_ID", "Is_Jailbroken", "Device_Threat_Rating"])
        w.writeheader()
        w.writerows(device_rows)

    email_rows = []
    for user in users.values():
        email_rows.append({
            "Email": user["email"],
            "Email_Age_Days": user["email_age_days"],
            "Email_Risk_Rating": email_risk_rating(user["email_age_days"]),
        })
    with open("email_risk_data.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Email", "Email_Age_Days", "Email_Risk_Rating"])
        w.writeheader()
        w.writerows(email_rows)

    # --- Trusted baseline table: the user's TRUE identity, as established at
    # account creation -- known_devices, home_location, typical spend. This
    # is PURELY ADDITIVE (no new random draws -- it just exports fields
    # make_user() already generates), so it doesn't change any existing
    # transaction data, fraud labels, or model training. Deliberately
    # decoupled from window transaction density: even a user with almost no
    # activity in our 180-day window has a well-defined true baseline here,
    # which is the whole point -- "known device" no longer depends on
    # having observed enough window activity to reconstruct it.
    baseline_rows = []
    for user in users.values():
        baseline_rows.append({
            "User_ID": user["user_id"],
            "Trusted_Device_IDs": ";".join(d["device_id"] for d in user["known_devices"]),
            "Home_Location": user["home_location"],
            "Typical_Transaction_Amount": round(2.71828 ** user["typical_amount_mu"], 2),
            "Account_Open_Days_Ago": user["account_open_days_ago"],
            "Lifetime_Transactions_Before_Window": user["lifetime_txns_before_window"],
        })
    with open("user_trusted_baseline.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "User_ID", "Trusted_Device_IDs", "Home_Location",
            "Typical_Transaction_Amount", "Account_Open_Days_Ago",
            "Lifetime_Transactions_Before_Window",
        ])
        w.writeheader()
        w.writerows(baseline_rows)

    print(f"Generated {len(rows)} transactions across {N_USERS} users.")
    print(f"Fraud rate: {sum(g['Fraudulent'] for g in ground_truth_rows) / len(ground_truth_rows):.3%}")
    print(f"Decline rate: {sum(1 for r in rows if r['Transaction_Status']=='Declined') / len(rows):.3%}")
    print(f"device_threat_intel.csv: {len(device_rows)} devices ({sum(1 for d in device_rows if d['Is_Jailbroken'])} jailbroken)")
    print(f"email_risk_data.csv: {len(email_rows)} emails")


if __name__ == "__main__":
    main()

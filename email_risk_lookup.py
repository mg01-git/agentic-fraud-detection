"""
Phase 3 tool: email_risk_lookup.

Thin wrapper around email_risk_data.csv -- the agent's callable form of
the "external, costly third-party email risk lookup" described in
METHODOLOGY.md Step 8. Mapped to the stolen_funding_instrument archetype.
"""

import pandas as pd

_df_cache = None


def _load_df():
    global _df_cache
    if _df_cache is None:
        _df_cache = pd.read_csv("email_risk_data.csv").set_index("Email")
    return _df_cache


def email_risk_lookup(email):
    """Returns {"found": bool, "email_age_days": int|None, "risk_rating": str|None}."""
    df = _load_df()
    if email not in df.index:
        return {"found": False, "email_age_days": None, "risk_rating": None}
    row = df.loc[email]
    return {
        "found": True,
        "email_age_days": int(row["Email_Age_Days"]),
        "risk_rating": row["Email_Risk_Rating"],
    }


if __name__ == "__main__":
    print(email_risk_lookup("user2617433@gmail.com"))
    print(email_risk_lookup("nonexistent@nowhere.com"))

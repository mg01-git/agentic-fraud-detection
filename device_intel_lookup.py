"""
Phase 3 tool: device_intel_lookup.

Thin wrapper around device_threat_intel.csv -- the agent's callable form
of the "external, costly third-party device reputation lookup" described
in METHODOLOGY.md Step 8. Mapped to the account_takeover archetype.
"""

import pandas as pd

_df_cache = None


def _load_df():
    global _df_cache
    if _df_cache is None:
        _df_cache = pd.read_csv("device_threat_intel.csv").set_index("Device_ID")
    return _df_cache


def device_intel_lookup(device_id):
    """Returns {"found": bool, "is_jailbroken": bool|None, "threat_rating": str|None}.
    found=False means this device has no third-party intel on record (e.g.
    a brand-new device with no history anywhere) -- a real, informative
    result, not an error."""
    df = _load_df()
    if device_id not in df.index:
        return {"found": False, "is_jailbroken": None, "threat_rating": None}
    row = df.loc[device_id]
    return {
        "found": True,
        "is_jailbroken": bool(row["Is_Jailbroken"]),
        "threat_rating": row["Device_Threat_Rating"],
    }


if __name__ == "__main__":
    print(device_intel_lookup("DEV-008159"))
    print(device_intel_lookup("DEV-999999"))

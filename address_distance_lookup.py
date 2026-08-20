"""
Phase 3 tool: address_distance_lookup.

Mimics an expensive, real-time distance computation between a Pay for
Purchase transaction's billing and shipping addresses -- e.g. a real
production system might call a geocoding/mapping API to get this. That's
WHY this is a tool and not a raw evidence field: Billing_Location and
Shipping_Location are already visible on the raw transaction row (cheap,
always-on), but the actual DISTANCE between them is only worth computing
for the cases that reach agent review, not for every single transaction.

Coarse bucketing, not literal lat-long math -- consistent with the rest of
this project's "plausible enough to reason about, not a real geocoding
system" approach. Three buckets:
  - "same_city": billing and shipping are the same city -- no distance signal.
  - "same_region": different city, same US region (see LOCATION_REGION in
    generate_synthetic_data.py) -- a believable in-region move (e.g. moved
    apartments, shipping to a nearby city).
  - "cross_region": different US region entirely -- the "drop address"
    pattern associated with stolen_funding_instrument cash-out (see
    generate_synthetic_data.py's billing/shipping block).

Only meaningful for Pay for Purchase transactions -- Billing_Location/
Shipping_Location are only populated for that transaction type (see
RAW_EVIDENCE_FIELDS / generate_synthetic_data.py). Calling this for any
other transaction type, or when either address is missing, returns an
explicit "not_applicable" bucket rather than guessing.
"""

from generate_synthetic_data import LOCATION_REGION


def address_distance_lookup(billing_location, shipping_location):
    """billing_location, shipping_location: strings (city names), as they
    appear in Billing_Location/Shipping_Location on the raw transaction row.
    Returns a dict describing the coarse distance bucket between them."""
    if not billing_location or not shipping_location:
        return {
            "distance_bucket": "not_applicable",
            "billing_region": None,
            "shipping_region": None,
            "explanation": "Billing and/or shipping location not populated on this transaction "
                            "(only set for Pay for Purchase) -- distance is not a meaningful check here.",
        }

    if billing_location == shipping_location:
        return {
            "distance_bucket": "same_city",
            "billing_region": LOCATION_REGION.get(billing_location),
            "shipping_region": LOCATION_REGION.get(shipping_location),
            "explanation": f"Billing and shipping are both {billing_location} -- no distance signal.",
        }

    billing_region = LOCATION_REGION.get(billing_location)
    shipping_region = LOCATION_REGION.get(shipping_location)

    if billing_region is not None and billing_region == shipping_region:
        return {
            "distance_bucket": "same_region",
            "billing_region": billing_region,
            "shipping_region": shipping_region,
            "explanation": f"Shipping to {shipping_location} differs from billing address {billing_location}, "
                            f"but both are in the {billing_region} region -- a believable in-region move.",
        }

    return {
        "distance_bucket": "cross_region",
        "billing_region": billing_region,
        "shipping_region": shipping_region,
        "explanation": f"Shipping to {shipping_location} ({shipping_region}) is a different US region than "
                        f"the billing address {billing_location} ({billing_region}) -- consistent with a "
                        f"'drop address' cash-out pattern, not an ordinary reason to ship elsewhere.",
    }


if __name__ == "__main__":
    print(address_distance_lookup("New York", "New York"))
    print(address_distance_lookup("New York", "Boston"))
    print(address_distance_lookup("New York", "Los Angeles"))
    print(address_distance_lookup("", ""))
    print(address_distance_lookup(None, "Boston"))

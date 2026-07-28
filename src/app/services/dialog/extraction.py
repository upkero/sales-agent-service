"""Slot extraction shared by the stages that read `service`/`quantity` from the
model's `data`.

QUALIFY fills these slots and PRESENT corrects them when the first guess did not
price, so the coercion rules live here once rather than being copied into both.
"""

from collections.abc import Sequence

# ops-core-api rejects a quantity above this with a 422 (`MAX_QUANTITY` in its
# pricing router). Mirroring the bound here means an implausible number never
# leaves the process: unbounded, "I want five thousand sessions" reaches the
# pricing API, comes back as a 422 whose error_code the gateway does not map,
# and lands in the unmapped-4xx branch as a plain PricingError — a 500 to the
# prospect. An unfilled slot instead just makes the agent ask again, which is
# what it does for every other unreadable answer.
_MAX_QUANTITY = 1000


def coerce_quantity(value: object) -> int | None:
    """A plausible integer quantity, or None. Tolerant of the model sending a
    numeric string; rejects bools (which are ints in Python), non-positive
    values, and anything the pricing service would refuse to quote."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= _MAX_QUANTITY else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value)
        return number if 1 <= number <= _MAX_QUANTITY else None
    return None


def canonical_service(catalogue: Sequence[str], extracted: str) -> str:
    """Snap a fuzzy match onto the exact catalogue name so the whole-string,
    case-insensitive price lookup is guaranteed to hit. Falls back to the raw
    value when nothing matches (PRESENT then recovers by offering the catalogue)."""
    for name in catalogue:
        if name.lower() == extracted.lower():
            return name
    return extracted

"""Slot extraction shared by the stages that read `service`/`quantity` from the
model's `data`.

QUALIFY fills these slots and PRESENT corrects them when the first guess did not
price, so the coercion rules live here once rather than being copied into both.
"""

from collections.abc import Sequence


def coerce_quantity(value: object) -> int | None:
    """A positive integer quantity, or None. Tolerant of the model sending a
    numeric string; rejects bools (which are ints in Python) and non-positive
    values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value)
        return number if number >= 1 else None
    return None


def canonical_service(catalogue: Sequence[str], extracted: str) -> str:
    """Snap a fuzzy match onto the exact catalogue name so the whole-string,
    case-insensitive price lookup is guaranteed to hit. Falls back to the raw
    value when nothing matches (PRESENT then recovers by offering the catalogue)."""
    for name in catalogue:
        if name.lower() == extracted.lower():
            return name
    return extracted

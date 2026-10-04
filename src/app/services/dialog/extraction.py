"""Slot extraction shared by the stages that read `service`/`quantity` from the
model's `data`.

QUALIFY fills these slots and PRESENT corrects them when the first guess did not
price, so the coercion rules live here once rather than being copied into both.
"""

import re
from collections.abc import Sequence

# ops-core-api rejects a quantity above this with a 422 (`MAX_QUANTITY` in its
# pricing router). Mirroring the bound here means an implausible number never
# leaves the process: unbounded, "I want five thousand sessions" reaches the
# pricing API, comes back as a 422 whose error_code the gateway does not map,
# and lands in the unmapped-4xx branch as a PricingRejectedError — a 502 to the
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


# Number words a customer types instead of digits, in the forms a chat actually uses.
# NOTE: a quantity written any other way ("a dozen", "восьмерых") is not recognised,
# so that change is ignored and the agent asks again; extend the table, not the rule.
_NUMBER_WORDS: dict[int, tuple[str, ...]] = {
    1: ("one", "один", "одна", "одно", "одного", "одному"),
    2: ("two", "два", "две", "двух"),
    3: ("three", "три", "трёх", "трех"),
    4: ("four", "четыре", "четырёх", "четырех"),
    5: ("five", "пять", "пяти"),
    6: ("six", "шесть", "шести"),
    7: ("seven", "семь", "семи"),
    8: ("eight", "восемь", "восьми"),
    9: ("nine", "девять", "девяти"),
    10: ("ten", "десять", "десяти"),
    12: ("twelve", "двенадцать", "двенадцати"),
    15: ("fifteen", "пятнадцать", "пятнадцати"),
    20: ("twenty", "двадцать", "двадцати"),
}


def mentions_quantity(text: str, quantity: int) -> bool:
    """Whether the customer's own words name `quantity`, as digits or a number word.

    A changed quantity the model reports must be one the customer said: asked
    about an offer, a model read "No thanks" as quantity 1 and the order was
    re-priced to a single session.
    """
    if quantity in {int(n) for n in re.findall(r"\d+", text)}:
        return True
    words = set(re.findall(r"\w+", text.lower()))
    return any(word in words for word in _NUMBER_WORDS.get(quantity, ()))


def canonical_service(catalogue: Sequence[str], extracted: str) -> str:
    """Snap a fuzzy match onto the exact catalogue name so the whole-string,
    case-insensitive price lookup is guaranteed to hit. Falls back to the raw
    value when nothing matches (PRESENT then recovers by offering the catalogue)."""
    for name in catalogue:
        if name.lower() == extracted.lower():
            return name
    return extracted

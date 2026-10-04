"""The check that keeps model arithmetic out of what the prospect hears.

Every price the agent states must be one ops-core-api returned. The prompts say so,
and a model still sometimes works a total out for itself (8 x 120 = 960.00, when
the real total after the volume discount is 864.00). So the reply is read back
after generation: any amount in it that no quote in hand contains is caught here,
and the stage replaces the reply rather than let the number through.
"""

import re
from collections.abc import Iterable
from decimal import Decimal

from src.app.contracts.pricing import PriceQuote

# "1,040.00", "1 040,00" (with a plain, no-break or narrow no-break space) or a
# bare "1040" / "864,5". Grouped thousands first, so "1,040.00" is not read as
# 1 and 40.00.
_NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:[,\s  ]\d{3})+|\d+)(?:[.,](\d{1,2}))?(?!\d)")

# NOTE: a number reads as an amount when it has cents or is 100 or more; smaller
# whole numbers are session counts, minutes and percentages. So an invented price
# under 100 with no cents ("95") slips through, and a year in a reply ("2026") is
# wrongly caught. The line holds because model arithmetic shows up in totals,
# and a total for two or more units of anything in the catalogue is 100 or more
# (and the model copies the cents it is given). If that ever stops being true,
# ask the model for the amounts it used in `data` and compare those instead.
_SMALLEST_WHOLE_AMOUNT = 100


def unquoted_amounts(
    reply: str, quotes: Iterable[PriceQuote | None], quantity: int | None = None
) -> list[Decimal]:
    """The amounts stated in `reply` that none of `quotes` contains.

    A discounted quote's subtotal counts as unquoted when its total is missing:
    "8 sessions come to 960.00" is the pre-discount figure passed off as the
    price, which is exactly the mistake this check exists for. The order's own
    `quantity` is never an amount: "500 sessions" before any quote is a count.
    """
    stated = _stated_amounts(reply)
    allowed: set[Decimal] = set() if quantity is None else {Decimal(quantity)}
    for quote in quotes:
        if quote is None:
            continue
        allowed.update((quote.unit_price, quote.discount_amount, quote.total, quote.discount_percent))
        allowed.add(Decimal(quote.quantity))
        if quote.discount_amount == 0 or quote.total in stated:
            allowed.add(quote.subtotal)
    return [value for value in stated if value not in allowed]  # Decimal equality: 864 == 864.00


def states_amount(reply: str, amount: Decimal) -> bool:
    """Whether `reply` states `amount` ("864", "864.00" and "864,00" all count)."""
    return amount in _stated_amounts(reply)


def _stated_amounts(reply: str) -> list[Decimal]:
    amounts: list[Decimal] = []
    for match in _NUMBER.finditer(reply):
        whole, cents = match.groups()
        value = Decimal(re.sub(r"\D", "", whole) + (f".{cents}" if cents else ""))
        if cents is not None or value >= _SMALLEST_WHOLE_AMOUNT:
            amounts.append(value)
    return amounts

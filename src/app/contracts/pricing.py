"""Internal DTOs for the pricing domain.

Frozen dataclasses, and money is `Decimal` end to end — never float. A quote is a
number a prospect is asked to pay, and binary floating point cannot represent
cents exactly. These types mirror ops-core-api's pricing contract but are
duplicated deliberately: importing them across the service boundary would couple
two deployables that are meant to version independently. The adapter is the one
place that translates.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PricingItem:
    service_name: str
    unit_price: Decimal
    description: str | None = None


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """The result of pricing a quantity of one service, discounts applied.

    Mirrors ops-core-api's PriceQuoteDTO field for field so the adapter is a
    straight mapping and the discount arithmetic stays on the owning service.
    """

    service_name: str
    unit_price: Decimal
    quantity: int
    subtotal: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    total: Decimal

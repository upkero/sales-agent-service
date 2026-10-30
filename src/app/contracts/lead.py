"""What the front desk needs to call a customer back."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Lead:
    """A closed conversation, as a person would act on it.

    Name and contact are whatever the customer typed, so either can be missing. The
    order is the accepted quote: the service, how many, and the total the customer
    was told.
    """

    conversation_id: str
    name: str | None
    contact: str | None
    service: str | None
    quantity: int | None
    total: Decimal | None

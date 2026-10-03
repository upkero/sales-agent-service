"""The conversation aggregate.

Unlike the other contracts this one is **mutable on purpose**: it is not a
transport DTO but the evolving state of a live dialogue, mutated once per turn as
the machine advances, slots fill and the presented quote is recorded. It still
lives in the inner contract layer (importing nothing outward) so the repository
port and the services can both speak in terms of it.
"""

from dataclasses import dataclass, field
from typing import Literal

from src.app.contracts.pricing import PriceQuote
from src.app.contracts.sales import SalesStage

MessageRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    role: MessageRole
    content: str


@dataclass(slots=True)
class Conversation:
    id: str
    stage: SalesStage = SalesStage.GREETING
    messages: list[ConversationMessage] = field(default_factory=list)

    # The real catalogue surfaced to this prospect, fetched once from ops-core-api
    # to ground the model's service extraction in names that actually price.
    offered_services: tuple[str, ...] = ()

    # Qualification slots, filled during QUALIFY and read by PRESENT.
    service: str | None = None
    quantity: int | None = None

    # The last quote shown to the prospect, so OBJECTION_HANDLING and UPSELL can
    # talk about a concrete number instead of re-deriving it.
    quote: PriceQuote | None = None

    # Whether the price has already been stated to the prospect. PRESENT presents
    # on one turn and reads the reaction on the next, so it needs to tell those
    # two turns apart.
    price_presented: bool = False

    # The upsell offer: a fresh quote at a higher quantity (the next volume tier),
    # and whether it has been put to the prospect yet. Same two-turn shape as PRESENT.
    upsell_quote: PriceQuote | None = None
    upsell_offered: bool = False

    # Robustness bookkeeping: how many turns in a row the model returned control
    # output the stage could not parse. Bounded escalation reads this.
    consecutive_parse_failures: int = 0

    def add_user(self, content: str) -> None:
        self.messages.append(ConversationMessage(role="user", content=content))

    def add_agent(self, content: str) -> None:
        self.messages.append(ConversationMessage(role="assistant", content=content))

    def recent(self, limit: int) -> list[ConversationMessage]:
        """The last `limit` messages, oldest first — what gets replayed to the model.

        Bounding this is what keeps a long conversation from growing the prompt
        (and the bill) without limit.
        """
        return self.messages[-limit:]

    @property
    def is_qualified(self) -> bool:
        """Both facts needed to fetch a price are known."""
        return bool(self.service) and self.quantity is not None

    @property
    def quote_is_stale(self) -> bool:
        """The prospect wants something no quote in hand prices: the order has to
        go back to ops-core-api before anyone states a number for it."""
        if self.service is None or self.quantity is None:
            return False
        service = self.service.lower()
        return not any(
            quote is not None and quote.service_name.lower() == service and quote.quantity == self.quantity
            for quote in (self.quote, self.upsell_quote)
        )

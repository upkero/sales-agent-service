"""In-memory stand-ins for everything outside this service.

FakePricingGateway is the second implementation of the PricingGateway port, which
is what makes the port more than decoration: if the stages can be driven by an
in-memory dict as easily as by HTTP, they really do not know which one they have.
StubLLM removes the one non-deterministic dependency so every stage's routing can
be asserted exactly.
"""

import json
import logging
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal

from src.app.bootstrap.container import ApplicationContainer
from src.app.contracts.lead import Lead
from src.app.contracts.llm.llm_message import LLMMessage
from src.app.contracts.llm.llm_response import LLMResponse
from src.app.contracts.pricing import PriceQuote
from src.app.exceptions.lead import LeadNotSavedError
from src.app.exceptions.pricing import PricingUnavailableError, ServiceNotFoundError
from src.app.interfaces.lead_gateway import LeadGateway
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.interfaces.pricing_gateway import PricingGateway

# The real services ops-core-api seeds, so the tests price what production prices.
DEFAULT_CATALOGUE: dict[str, Decimal] = {
    "Deep Tissue Massage": Decimal("120.00"),
    "Nutrition Coaching": Decimal("95.00"),
    "Physiotherapy Assessment": Decimal("140.00"),
    "Sports Recovery Session": Decimal("110.00"),
    "Meeting Room Hire": Decimal("60.00"),
}

_CENTS = Decimal("0.01")
# Mirrors ops-core-api's QuantityTierDiscountPolicy: 10% from 6 units, 15% from 20.
_TIERS: tuple[tuple[int, Decimal], ...] = ((6, Decimal("10")), (20, Decimal("15")))


def _to_cents(amount: Decimal) -> Decimal:
    return amount.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _discount_percent(quantity: int) -> Decimal:
    percent = Decimal("0")
    for minimum, tier_percent in _TIERS:
        if quantity >= minimum:
            percent = tier_percent
    return percent


def compute_quote(service_name: str, unit_price: Decimal, quantity: int) -> PriceQuote:
    """The same arithmetic ops-core-api's PricingService performs, so a test can
    assert the agent quotes exactly what the real service would."""
    subtotal = _to_cents(unit_price * quantity)
    discount_percent = _discount_percent(quantity)
    discount_amount = _to_cents(subtotal * discount_percent / Decimal("100"))
    return PriceQuote(
        service_name=service_name,
        unit_price=unit_price,
        quantity=quantity,
        subtotal=subtotal,
        discount_percent=discount_percent,
        discount_amount=discount_amount,
        total=subtotal - discount_amount,
    )


class FakePricingGateway(PricingGateway):
    def __init__(self, catalogue: dict[str, Decimal] | None = None) -> None:
        self._catalogue = dict(catalogue or DEFAULT_CATALOGUE)
        self.calls: list[tuple[str, ...]] = []

    async def quote(self, service: str, quantity: int) -> PriceQuote:
        self.calls.append(("quote", service, str(quantity)))
        name, unit_price = self._lookup(service)
        return compute_quote(name, unit_price, quantity)

    async def list_services(self) -> Sequence[str]:
        self.calls.append(("list_services",))
        return list(self._catalogue)

    async def ping(self) -> bool:
        return True

    def _lookup(self, service: str) -> tuple[str, Decimal]:
        for name, price in self._catalogue.items():
            if name.lower() == service.lower():  # ops-core-api matches case-insensitively
                return name, price
        raise ServiceNotFoundError(f"No pricing for '{service}'.")


class UnavailablePricingGateway(PricingGateway):
    """Every call fails the way an unreachable ops-core-api fails."""

    async def quote(self, service: str, quantity: int) -> PriceQuote:
        raise PricingUnavailableError()

    async def list_services(self) -> Sequence[str]:
        raise PricingUnavailableError()

    async def ping(self) -> bool:
        return False  # models an unreachable ops-core-api


class FakeLeadGateway(LeadGateway):
    """Keeps the leads it was given, so a test can read what the front desk would see."""

    def __init__(self) -> None:
        self.leads: list[Lead] = []

    async def save_lead(self, lead: Lead) -> None:
        self.leads.append(lead)


class UnavailableLeadGateway(LeadGateway):
    """Every save fails the way an unreachable ops-core-api fails."""

    def __init__(self) -> None:
        self.attempts = 0

    async def save_lead(self, lead: Lead) -> None:
        self.attempts += 1
        raise LeadNotSavedError()


# What every StubLLM reply reports having used, so a test can assert the tokens a
# turn adds up to without the stub having to know what a token is.
STUB_USAGE = {"prompt_tokens": 11, "completion_tokens": 7}


class StubLLM(LLMClient):
    """A scripted LLM. `script` is one of:

    - a str: returned for every call (handy for garbage / fixed replies),
    - a list[str]: a queue, one popped per call (a turn is one call unless the
      base stage has to re-ask),
    - a callable(messages) -> str: for responses that depend on the prompt.
    """

    def __init__(self, script: str | list[str] | Callable[[Sequence[LLMMessage]], str]) -> None:
        self._script = script
        self.calls: list[list[LLMMessage]] = []
        self.json_modes: list[bool] = []

    @property
    def model_name(self) -> str:
        return "stub-model"

    @property
    def provider_name(self) -> str:
        return "stub"

    async def complete(self, messages: Sequence[LLMMessage], *, json_mode: bool = False) -> LLMResponse:
        self.calls.append(list(messages))
        self.json_modes.append(json_mode)
        return LLMResponse(content=self._pick(messages), usage=STUB_USAGE)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    def _pick(self, messages: Sequence[LLMMessage]) -> str:
        script = self._script
        if callable(script):
            return script(messages)
        if isinstance(script, str):
            return script
        if not script:
            raise AssertionError("StubLLM script exhausted: more turns than scripted responses.")
        return script.pop(0)


def control(reply: str, **data: object) -> str:
    """Build the JSON control object a well-behaved model would return."""
    return json.dumps({"reply": reply, "data": data})


def verdict(take: bool) -> str:
    """What the upsell-answer reading call returns: did the reply take the offer."""
    return json.dumps({"take": take})


def build_container(llm: LLMClient, pricing: PricingGateway, leads: LeadGateway | None = None) -> ApplicationContainer:
    """A real container with the external dependencies pre-injected.

    Presetting the cached_property slots means the real wiring (the stage
    registry, the orchestrator, the in-memory store) is exercised end to end,
    while the LLM and ops-core-api (prices and leads) are the only things faked.
    """
    container = ApplicationContainer()
    container.__dict__["llm_client"] = llm
    container.__dict__["pricing_gateway"] = pricing
    container.__dict__["lead_gateway"] = leads or FakeLeadGateway()
    return container


# Attributes every LogRecord carries (time, pid, thread...). Leaving them out of a PII
# check matters: their digits can contain any short number, "555" included.
_STANDARD_LOG_ATTRS = frozenset(vars(logging.makeLogRecord({})))


def logged_text(records: Sequence[logging.LogRecord]) -> str:
    """What a log line could leak: each message and the extras the code attached."""
    return str([(r.getMessage(), {k: v for k, v in vars(r).items() if k not in _STANDARD_LOG_ATTRS}) for r in records])

"""The dialogue stage skeleton — Template Method.

`handle()` fixes the shape of every turn: prepare any data the stage needs, build
the prompt in a fixed order, ask the model, absorb what it extracted, then decide
where the machine goes next. A concrete stage overrides only the parts that differ
between steps of the funnel — its `directive()` (what to instruct the model) and
its `route()` (when to advance) — and optionally the `prepare()`/`absorb()`/
`data_spec()` hooks. It can never reshuffle the skeleton, which is what keeps the
control flow identical and the orchestrator free of per-stage branching.
"""

from abc import ABC, abstractmethod
from logging import getLogger
from typing import ClassVar

from src.app.contracts.conversation import Conversation
from src.app.contracts.llm.llm_message import LLMMessage
from src.app.contracts.pricing import PriceQuote
from src.app.contracts.sales import SalesStage, StageResult
from src.app.core.settings.agent import SalesAgentSettings
from src.app.interfaces.llm.llm_client import LLMClient
from src.app.llm.prompt_builder import PromptBuilder
from src.app.services.dialog.decision import AgentDecision

logger = getLogger(__name__)

_LANGUAGE_NAMES = {"en": "English", "ru": "Russian"}

_STRICT_REASK = (
    "Your previous reply was not a single valid JSON object. Reply again with ONLY "
    "the JSON object described above — no prose, no code fences — and nothing else."
)


class DialogueStage(ABC):
    #: Which stage of the funnel this class owns. Set by every concrete subclass.
    stage: ClassVar[SalesStage]

    def __init__(self, llm: LLMClient, settings: SalesAgentSettings) -> None:
        self._llm = llm
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Template Method: the fixed skeleton. Do not override.
    # ------------------------------------------------------------------ #
    async def handle(self, conversation: Conversation) -> StageResult:
        await self.prepare(conversation)
        messages = self._compose_prompt(conversation)
        decision = await self._decide(messages)

        if decision is None:
            # The model broke the control contract. Never route on a guess.
            return self._on_parse_failure(conversation)

        conversation.consecutive_parse_failures = 0
        self.absorb(conversation, decision)
        next_stage = self.route(conversation, decision)
        return StageResult(reply=decision.reply, current_stage=self.stage, next_stage=next_stage)

    # ------------------------------------------------------------------ #
    # Overridable steps.
    # ------------------------------------------------------------------ #
    async def prepare(self, conversation: Conversation) -> None:  # noqa: B027 (deliberate optional hook)
        """Hook: fetch anything the directive needs (e.g. a live price). No-op by default.

        Deliberately concrete-and-empty, not abstract: most stages need no
        preparation, and forcing all six to write `pass` would be noise. This is
        the standard Template Method optional-hook shape.
        """

    @abstractmethod
    def directive(self, conversation: Conversation) -> str:
        """The stage-specific instructions — the only part of the system prompt a stage supplies."""

    def data_spec(self) -> str:
        """Description of the `data` fields this stage wants back. Empty = none."""
        return ""

    def absorb(self, conversation: Conversation, decision: AgentDecision) -> None:  # noqa: B027 (deliberate optional hook)
        """Hook: persist slots the model extracted. No-op by default (see prepare)."""

    @abstractmethod
    def route(self, conversation: Conversation, decision: AgentDecision) -> SalesStage:
        """The transition condition: the stage the conversation is in next turn."""

    # ------------------------------------------------------------------ #
    # Shared machinery.
    # ------------------------------------------------------------------ #
    def _compose_prompt(self, conversation: Conversation) -> list[LLMMessage]:
        builder = PromptBuilder().system(self._system_prompt(conversation))
        builder.extend(
            LLMMessage(role=message.role, content=message.content)
            for message in conversation.recent(self._settings.history_limit)
        )
        return builder.build()

    def _system_prompt(self, conversation: Conversation) -> str:
        # Fixed section order (persona -> task -> output contract): the persona and
        # the hard output rule bracket the free-form stage directive so a verbose
        # directive cannot bury either. Mirrors the ordering discipline in the
        # sibling voice-agent's dialog flow.
        sections = (self._persona(), self.directive(conversation), self._output_contract())
        return "\n\n".join(section.strip() for section in sections if section.strip())

    def _persona(self) -> str:
        language = _LANGUAGE_NAMES.get(self._settings.language, "English")
        return (
            f"You are {self._settings.name}, a warm, concise sales representative for "
            f"{self._settings.company}. You reply only in {language}. You are helpful and "
            "human, never pushy or robotic. Keep every reply to one or two short sentences."
        )

    def _output_contract(self) -> str:
        spec = self.data_spec()
        data_line = f'  "data": {{ {spec} }}' if spec else '  "data": {}'
        return (
            "Respond with a single JSON object and nothing else, of the form:\n"
            "{\n"
            '  "reply": "<what you say to the customer>",\n'
            f"{data_line}\n"
            "}"
        )

    async def _decide(self, messages: list[LLMMessage]) -> AgentDecision | None:
        response = await self._llm.complete(messages, json_mode=True)
        decision = AgentDecision.parse(response.content)
        if decision is not None:
            return decision

        # One strict re-ask fixes the common single hiccup without a visible stall.
        retry = await self._llm.complete([*messages, LLMMessage(role="system", content=_STRICT_REASK)], json_mode=True)
        return AgentDecision.parse(retry.content)

    def _on_parse_failure(self, conversation: Conversation) -> StageResult:
        conversation.consecutive_parse_failures += 1
        if conversation.consecutive_parse_failures >= self._settings.max_parse_failures:
            logger.error(
                "LLM control output unparseable %d turns running; escalating to hand-off",
                conversation.consecutive_parse_failures,
                extra={"conversation_id": conversation.id, "stage": self.stage.value},
            )
            return StageResult(
                reply=self._handoff_message(),
                current_stage=self.stage,
                next_stage=self.stage,
                handoff=True,
            )

        logger.warning(
            "LLM control output unparseable; staying in stage and re-asking next turn",
            extra={
                "conversation_id": conversation.id,
                "stage": self.stage.value,
                "consecutive_parse_failures": conversation.consecutive_parse_failures,
            },
        )
        return StageResult(reply=self._clarifier_message(), current_stage=self.stage, next_stage=self.stage)

    @staticmethod
    def _describe_quote(quote: PriceQuote) -> str:
        """The quote as plain facts for the model to phrase — never shown raw to
        the customer. Shared by the three stages that talk money."""
        if quote.discount_percent > 0:
            return (
                f"{quote.quantity} x {quote.service_name} at {quote.unit_price} each is a "
                f"{quote.subtotal} subtotal; a {quote.discount_percent}% volume discount takes off "
                f"{quote.discount_amount}, for a total of {quote.total}."
            )
        return (
            f"{quote.quantity} x {quote.service_name} at {quote.unit_price} each, "
            f"for a total of {quote.total}. No volume discount applies at this quantity."
        )

    def _clarifier_message(self) -> str:
        if self._settings.language == "ru":
            return "Извините, я не расслышал. Не могли бы вы повторить?"
        return "Sorry, I didn't quite catch that — could you say it once more?"

    def _handoff_message(self) -> str:
        if self._settings.language == "ru":
            return "Давайте я передам вас специалисту, который свяжется с вами и всё уточнит."
        return "Let me take your details and have a specialist follow up with you directly."

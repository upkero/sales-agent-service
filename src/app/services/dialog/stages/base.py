"""The dialogue stage skeleton — Template Method.

`handle()` fixes the shape of every turn: prepare any data the stage needs, build
the prompt in a fixed order, ask the model, absorb what it extracted, then decide
where the machine goes next. A concrete stage overrides only the parts that differ
between steps of the funnel — its `directive()` (what to instruct the model) and
its `route()` (when to advance) — and optionally the `prepare()`/`absorb()`/
`data_spec()` hooks. It can never reshuffle the skeleton, which is what keeps the
control flow identical and the orchestrator free of per-stage branching.

None of the text itself is here. Persona, output contract, re-ask and every
per-stage directive are Markdown in `prompts/`; what a stage supplies is which
prompt and which values, never the wording. The two sentences the customer reads
verbatim when the model is unusable come from `messages/`, which is the same
separation from the other side: `prompts/` is English because that is where the
instruction is best followed, `messages/` is per-language because nobody
paraphrases it.
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
from src.app.messages import get_message
from src.app.prompts import Prompt, get_prompt
from src.app.services.dialog.decision import AgentDecision

logger = getLogger(__name__)

# The prompt files are English; this is what the {reply_language} placeholder in
# persona.md is filled with. The instruction is never translated — only the
# language it names changes.
_LANGUAGE_NAMES = {"en": "English", "ru": "Russian"}

_PERSONA = get_prompt("persona")
_OUTPUT_CONTRACT = get_prompt("output_contract")
_STRICT_REASK = get_prompt("strict_reask")


class DialogueStage(ABC):
    #: Which stage of the funnel this class owns. Set by every concrete subclass.
    stage: ClassVar[SalesStage]

    #: Every prompt this stage can send. Only used to say, in a log line, which
    #: revision of which text produced a given turn — so an answer that reads
    #: wrong six weeks from now can be pinned to the wording that was live.
    prompts: ClassVar[tuple[Prompt, ...]] = ()

    def __init__(self, llm: LLMClient, settings: SalesAgentSettings) -> None:
        self._llm = llm
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Template Method: the fixed skeleton. Do not override.
    # ------------------------------------------------------------------ #
    async def handle(self, conversation: Conversation) -> StageResult:
        await self.prepare(conversation)
        messages = self._compose_prompt(conversation)
        # Debug, not info: this is one line per turn and its only reader is
        # somebody already looking at a specific conversation.
        logger.debug(
            "Sending the %s prompt set",
            self.stage.value,
            extra={"conversation_id": conversation.id, "prompt_ids": self._prompt_ids()},
        )
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
        return _PERSONA.render(
            agent_name=self._settings.name,
            company=self._settings.company,
            reply_language=_LANGUAGE_NAMES.get(self._settings.language, "English"),
        )

    def _output_contract(self) -> str:
        # Padded here rather than in the template so a stage that wants no data
        # gets `"data": {}` and not `"data": {  }`.
        spec = self.data_spec()
        return _OUTPUT_CONTRACT.render(data_fields=f" {spec} " if spec else "")

    def _prompt_ids(self) -> list[str]:
        return [prompt.id for prompt in (_PERSONA, _OUTPUT_CONTRACT, *self.prompts)]

    async def _decide(self, messages: list[LLMMessage]) -> AgentDecision | None:
        response = await self._llm.complete(messages, json_mode=True)
        decision = AgentDecision.parse(response.content)
        if decision is not None:
            return decision

        # One strict re-ask fixes the common single hiccup without a visible stall.
        reask = LLMMessage(role="system", content=_STRICT_REASK.text)
        retry = await self._llm.complete([*messages, reask], json_mode=True)
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
        return get_message(self._settings.language, "clarifier")

    def _handoff_message(self) -> str:
        return get_message(self._settings.language, "handoff")

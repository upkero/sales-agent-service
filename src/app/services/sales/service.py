"""The orchestrator: it runs the machine, it is not the machine.

Notice what is *not* here — there is no `if stage == GREETING … elif …` ladder.
The current stage is looked up in a registry and asked to `handle()` itself; the
stage decides what to say and where to go. Adding a stage is a new class plus one
registry entry, and this file never changes. The one thing the orchestrator owns
is the guard rail: it refuses any transition a stage was not allowed to make.
"""

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from logging import getLogger
from uuid import uuid4
from weakref import WeakValueDictionary

from src.app.contracts.conversation import Conversation, Language
from src.app.contracts.sales import ALLOWED_TRANSITIONS, SalesStage, TurnOutcome
from src.app.exceptions.dialog import InvalidStageTransitionError
from src.app.interfaces.conversation_repository import ConversationRepository
from src.app.services.dialog.stages.base import DialogueStage

logger = getLogger(__name__)


class SalesService:
    def __init__(
        self,
        conversations: ConversationRepository,
        stages: Mapping[SalesStage, DialogueStage],
        default_language: Language = "en",
    ) -> None:
        self._conversations = conversations
        self._stages = stages
        self._default_language = default_language
        # One lock per conversation in flight. Weak values: a lock disappears with
        # the last turn holding or awaiting it, so the map never outgrows the
        # number of conversations being talked to right now.
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def take_turn(
        self,
        conversation_id: str | None,
        message: str,
        language: Language | None = None,
    ) -> TurnOutcome:
        if not conversation_id:
            return await self._take_turn(conversation_id, message, language)
        # Two turns on one conversation (a double-click, a retry racing the first
        # attempt) each work on their own copy of it; run unserialised, the later
        # save() would silently drop the other turn. They take turns instead.
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = self._locks[conversation_id] = asyncio.Lock()
        async with lock:
            return await self._take_turn(conversation_id, message, language)

    async def _take_turn(
        self,
        conversation_id: str | None,
        message: str,
        language: Language | None,
    ) -> TurnOutcome:
        conversation = await self._load_or_start(conversation_id, message)
        if language is not None:
            # The caller knows (the site's language switch); that beats any guess,
            # for this turn and every later one that does not say otherwise.
            conversation.language = language
        conversation.add_user(message)

        current_stage = conversation.stage
        order_before = (conversation.service, conversation.quantity)
        result = await self._stages[current_stage].handle(conversation)

        order_changed = (conversation.service, conversation.quantity) != order_before
        if result.next_stage is SalesStage.PRESENT and order_changed and conversation.quote_is_stale:
            # The prospect just named an order no quote prices: both slots were filled
            # in QUALIFY, or they changed the quantity or service after hearing a price.
            # The reply just written was composed without a price for it, so it can
            # only stall ("let me check") or, worse, work the total out itself. PRESENT
            # takes this same turn instead: it asks ops-core-api first, and its reply,
            # which states the real total, replaces the one without it.
            self._guard_transition(conversation, current_stage, result.next_stage)
            current_stage = SalesStage.PRESENT
            conversation.stage = current_stage
            result = await self._stages[current_stage].handle(conversation)

        # A stage that only read the prospect's reaction hands the turn to the stage it
        # moved to, whose reply replaces the one that had nothing to say: the upsell
        # offer right after "sounds good". A reaction can pass through two: "no thanks,
        # six is enough" read as a concern is answered and settled by OBJECTION_HANDLING,
        # and the offer still belongs in this reply, not on the prospect's next message.
        # Bounded by the number of stages; the funnel only moves forward anyway.
        for _ in range(len(self._stages)):
            if not self._stages[current_stage].hands_over_on_advance or result.next_stage is current_stage:
                break
            self._guard_transition(conversation, current_stage, result.next_stage)
            current_stage = result.next_stage
            conversation.stage = current_stage
            result = await self._stages[current_stage].handle(conversation)

        self._guard_transition(conversation, current_stage, result.next_stage)

        conversation.add_agent(result.reply)
        conversation.stage = result.next_stage
        await self._conversations.save(conversation)

        return TurnOutcome(
            conversation_id=conversation.id,
            reply=result.reply,
            stage=result.next_stage,
            handoff=result.handoff,
            done=conversation.closed,
        )

    async def _load_or_start(self, conversation_id: str | None, first_message: str) -> Conversation:
        if conversation_id:
            existing = await self._conversations.get(conversation_id)
            if existing is not None:
                # A copy, so a turn is all or nothing: the stages mutate it freely
                # and only save() at the end publishes it. Should the LLM or
                # ops-core-api fail halfway, the stored conversation keeps neither
                # the message nor a stage it advanced to, and a client that resends
                # after the 429/503 does not get its message in the history twice.
                return deepcopy(existing)
        # No id, or an id we have never seen: begin a fresh conversation under an
        # id we mint. A known id continues that conversation; anything else starts
        # a new one and gets told its real id back. The id is the caller's handle
        # on its own dialogue, not a way to reach someone else's — it is
        # unguessable precisely because the server chose it. Honouring the
        # caller's id here instead would let two clients that both sent "1" land
        # in the same conversation, which nothing about "continue my dialogue"
        # needs: that feature wants a *lookup*, not a create.
        return Conversation(id=str(uuid4()), language=self._language_of(first_message))

    def _language_of(self, message: str) -> Language:
        """Any Cyrillic letter means Russian, any other letter English. Only a
        message with no letters at all ("👋", "3") leaves it to AGENT_LANGUAGE.

        Settled once, on the first message: "hello" from a visitor to an English
        page is English even when the service is configured for Russian, and a
        Russian name typed later ("Алекс, ok") does not switch the conversation.
        """
        if any("Ѐ" <= char <= "ӿ" for char in message):
            return "ru"
        if any(char.isalpha() for char in message):
            return "en"
        return self._default_language

    @staticmethod
    def _guard_transition(conversation: Conversation, current: SalesStage, requested: SalesStage) -> None:
        """Reject any move a stage was not permitted to make.

        A violation is a bug in a stage's route(), not a user error, so it is
        raised as a typed 500 (which the API renders as the uniform error
        envelope, not a stacktrace) rather than asserted — an assert would vanish
        under `python -O` and, if it did fire, escape as an unhandled 500.
        """
        if requested not in ALLOWED_TRANSITIONS[current]:
            logger.error(
                "Illegal stage transition attempted",
                extra={
                    "conversation_id": conversation.id,
                    "from_stage": current.value,
                    "to_stage": requested.value,
                },
            )
            raise InvalidStageTransitionError(
                extra={"from_stage": current.value, "to_stage": requested.value},
            )

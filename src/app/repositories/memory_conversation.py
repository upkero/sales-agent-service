"""In-memory implementation of the ConversationRepository port.

A dict held on the container, so all requests in one process share it. That is
enough for this service's scale and matches how the sibling voice-agent keeps its
session state — no database to run, back up or migrate.

# ponytail: single process, lost on restart, not shared across workers. The
# Dockerfile pins --workers 1 so a conversation never lands on a process that
# cannot see it; when horizontal scale matters, add a DB-backed implementation of
# this same port and swap it in the container — no service changes.
"""

from src.app.contracts.conversation import Conversation
from src.app.interfaces.conversation_repository import ConversationRepository


class InMemoryConversationRepository(ConversationRepository):
    def __init__(self) -> None:
        self._store: dict[str, Conversation] = {}

    async def get(self, conversation_id: str) -> Conversation | None:
        return self._store.get(conversation_id)

    async def save(self, conversation: Conversation) -> None:
        # The aggregate is mutated in place by the service, so this is mostly a
        # first-turn insert; storing the reference keeps get/save symmetric and
        # ready for a serialising implementation to replace it.
        self._store[conversation.id] = conversation

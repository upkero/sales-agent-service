from abc import ABC, abstractmethod

from src.app.contracts.conversation import Conversation


class ConversationRepository(ABC):
    """Where a conversation is kept between turns.

    A port with a single in-memory implementation today, and that is a deliberate
    choice, not an unfinished one: it is the real seam at which a Postgres- or
    Redis-backed store would slot in (Open/Closed) when conversations need to
    outlive one process. Keeping it an abstraction now means that swap is a new
    class in `repositories/` and one line in the container, touching no service.
    """

    @abstractmethod
    async def get(self, conversation_id: str) -> Conversation | None:
        """The stored conversation, or None if this id has never been seen."""

    @abstractmethod
    async def save(self, conversation: Conversation) -> None:
        """Persist the conversation's current state."""

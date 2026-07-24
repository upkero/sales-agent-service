"""In-memory implementation of the ConversationRepository port.

A dict held on the container, so all requests in one process share it. That is
enough for this service's scale and matches how the sibling voice-agent keeps its
session state — no database to run, back up or migrate.

Memory is bounded two ways so an abandoned conversation cannot leak forever:
  * a TTL — a conversation not touched within `ttl_seconds` is evicted, and
  * a hard cap — beyond `max_entries` the least-recently-active one is dropped.

Eviction is lazy on read (an expired conversation reads as absent) and swept on
write (each save purges what has expired and enforces the cap). No background
timer: a fully idle process keeps a few expired entries until the next save, which
is harmless because idle means nothing is growing.

# ponytail: sweep is O(expired) on the OrderedDict front per save — fine at this
# scale. If write throughput ever makes that show up, move to a DB/Redis store
# behind this same port (which is the whole reason it is a port).
"""

import time
from collections import OrderedDict
from collections.abc import Callable

from src.app.contracts.conversation import Conversation
from src.app.interfaces.conversation_repository import ConversationRepository


class InMemoryConversationRepository(ConversationRepository):
    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        # monotonic by default: measuring elapsed time, so it must be immune to
        # wall-clock jumps. Injectable so tests can advance time deterministically.
        self._clock = clock
        # Ordered by last activity; the front is the least-recently-active entry,
        # which is both the first to expire and the first to evict under the cap.
        self._store: OrderedDict[str, tuple[float, Conversation]] = OrderedDict()

    async def get(self, conversation_id: str) -> Conversation | None:
        entry = self._store.get(conversation_id)
        if entry is None:
            return None
        last_seen, conversation = entry
        if self._is_expired(last_seen):
            del self._store[conversation_id]
            return None
        return conversation

    async def save(self, conversation: Conversation) -> None:
        now = self._clock()
        self._store[conversation.id] = (now, conversation)
        self._store.move_to_end(conversation.id)  # mark as most-recently-active
        self._evict(now)

    def size(self) -> int:
        """How many conversations are currently held. Handy for a metric or a test."""
        return len(self._store)

    def _is_expired(self, last_seen: float) -> bool:
        return self._clock() - last_seen > self._ttl

    def _evict(self, now: float) -> None:
        # Front is oldest: drop expired entries until the first live one, then
        # enforce the hard cap by dropping the least-recently-active.
        while self._store:
            conversation_id, (last_seen, _) = next(iter(self._store.items()))
            if now - last_seen <= self._ttl:
                break
            del self._store[conversation_id]
        while len(self._store) > self._max:
            self._store.popitem(last=False)

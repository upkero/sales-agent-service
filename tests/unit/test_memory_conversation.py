"""The in-memory store's bounds, driven by a fake clock so time is deterministic."""

from src.app.contracts.conversation import Conversation
from src.app.repositories.memory_conversation import InMemoryConversationRepository


class FakeClock:
    """A hand-cranked monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _store(clock: FakeClock, *, ttl: float = 100.0, max_entries: int = 1000) -> InMemoryConversationRepository:
    return InMemoryConversationRepository(ttl_seconds=ttl, max_entries=max_entries, clock=clock)


async def test_a_saved_conversation_is_retrievable() -> None:
    clock = FakeClock()
    store = _store(clock)
    await store.save(Conversation(id="c1"))

    assert (await store.get("c1")) is not None


async def test_a_conversation_expires_after_its_ttl_on_read() -> None:
    clock = FakeClock()
    store = _store(clock, ttl=100.0)
    await store.save(Conversation(id="c1"))

    clock.advance(101.0)  # past the TTL, no activity in between

    assert (await store.get("c1")) is None


async def test_activity_refreshes_the_ttl() -> None:
    clock = FakeClock()
    store = _store(clock, ttl=100.0)
    conversation = Conversation(id="c1")
    await store.save(conversation)

    clock.advance(80.0)
    await store.save(conversation)  # touched again — the clock resets
    clock.advance(80.0)  # 160s since first save, but only 80s since the last

    assert (await store.get("c1")) is not None


async def test_a_later_save_sweeps_expired_entries() -> None:
    clock = FakeClock()
    store = _store(clock, ttl=100.0)
    await store.save(Conversation(id="old"))

    clock.advance(101.0)
    await store.save(Conversation(id="new"))  # this write purges the expired "old"

    # "old" is gone even though nobody ever read it back — the leak is closed.
    assert store.size() == 1
    assert (await store.get("new")) is not None


async def test_the_hard_cap_evicts_the_least_recently_active() -> None:
    clock = FakeClock()
    store = _store(clock, ttl=10_000.0, max_entries=2)

    for conversation_id in ("a", "b"):
        await store.save(Conversation(id=conversation_id))
    clock.advance(1.0)
    await store.save(Conversation(id="a"))  # touch "a" so "b" is now the oldest
    await store.save(Conversation(id="c"))  # over the cap of 2 -> evict "b"

    assert store.size() == 2
    assert (await store.get("b")) is None
    assert (await store.get("a")) is not None
    assert (await store.get("c")) is not None

from abc import ABC, abstractmethod
from collections.abc import Sequence

from src.app.contracts.llm.llm_message import LLMMessage
from src.app.contracts.llm.llm_response import LLMResponse


class LLMClient(ABC):
    """The port the dialogue stages speak to.

    `json_mode` is part of the port, not an implementation detail: the whole
    control protocol depends on the model returning a JSON object, and a client
    that cannot ask for one would silently make the state machine fragile. A test
    double satisfies it by returning canned JSON.
    """

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def complete(self, messages: Sequence[LLMMessage], *, json_mode: bool = False) -> LLMResponse: ...

    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...

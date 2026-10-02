import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any

from openai import OpenAIError

from src.app.contracts.llm.llm_message import LLMMessage
from src.app.contracts.llm.llm_response import LLMResponse
from src.app.core.settings.llm import LLMSettings
from src.app.exceptions.llm import LLMGenerationError, LLMInputError
from src.app.interfaces.llm.llm_client import LLMClient

_USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")

class OpenAICompatibleLLMClient(LLMClient):
    """One client for every OpenAI-shaped provider (OpenAI, Ollama, others).

    The provider is chosen entirely by base_url in the factory; nothing here
    names one, which is what lets the same code talk to a local Ollama in dev and
    a hosted model in production.
    """

    def __init__(self, *, settings: LLMSettings, client: Any) -> None:
        self._settings = settings
        self._client = client
        self._ping_result: bool | None = None
        self._ping_expires = 0.0
        self._ping_lock = asyncio.Lock()
        # Readiness is polled every ~10 s per caller and each probe is a real request
        # to a provider whose latency swings from 0.3 s to several, so an answer is
        # reused: long when healthy, short when not, so a recovery shows on the next poll.

    @property
    def model_name(self) -> str:
        return self._settings.model

    @property
    def provider_name(self) -> str:
        return self._settings.provider

    async def complete(self, messages: Sequence[LLMMessage], *, json_mode: bool = False) -> LLMResponse:
        if not messages:
            raise LLMInputError("LLM messages must not be empty.")

        try:
            completion = await self._client.chat.completions.create(
                **self._completion_params(messages, json_mode),
            )
        except OpenAIError as exc:
            raise LLMGenerationError("LLM provider request failed.") from exc

        return self._build_response(completion)

    async def ping(self) -> bool:
        if self._ping_result is not None and time.monotonic() < self._ping_expires:
            return self._ping_result
        # One probe at a time: pollers arriving together share its answer instead
        # of each sending their own request.
        async with self._ping_lock:
            if self._ping_result is not None and time.monotonic() < self._ping_expires:
                return self._ping_result
            try:
                await self._client.models.list()
                healthy = True
            except OpenAIError:
                healthy = False
            self._ping_result = healthy
            self._ping_expires = time.monotonic() + (
                self._settings.ping_ok_ttl_seconds if healthy else self._settings.ping_failed_ttl_seconds
            )
            return healthy

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            await close()

    def _completion_params(self, messages: Sequence[LLMMessage], json_mode: bool) -> dict[str, object]:
        params: dict[str, object] = {
            "model": self._settings.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if json_mode:
            # Make valid JSON the model's default output, so the stages' control
            # parser is dealing with the rare malformed case rather than the norm.
            params["response_format"] = {"type": "json_object"}
        if self._settings.temperature is not None:
            params["temperature"] = self._settings.temperature
        if self._settings.max_tokens is not None:
            params["max_tokens"] = self._settings.max_tokens
        if self._settings.reasoning_effort is not None:
            params["reasoning_effort"] = self._settings.reasoning_effort
        return params

    def _build_response(self, completion: Any) -> LLMResponse:
        choice = self._first_choice(completion)
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        if content is None:
            raise LLMGenerationError("LLM provider returned empty message content.")

        return LLMResponse(
            content=content,
            model=getattr(completion, "model", None) or self.model_name,
            finish_reason=getattr(choice, "finish_reason", None),
            usage=self._usage_to_mapping(getattr(completion, "usage", None)),
            metadata=self._metadata(completion),
        )

    @staticmethod
    def _first_choice(completion: Any) -> Any:
        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise LLMGenerationError("LLM provider returned no choices.")
        return choices[0]

    @staticmethod
    def _usage_to_mapping(usage: Any) -> Mapping[str, int] | None:
        if usage is None:
            return None
        result: dict[str, int] = {}
        for field in _USAGE_FIELDS:
            value = getattr(usage, field, None)
            if isinstance(value, int):
                result[field] = value
        return result or None

    @staticmethod
    def _metadata(completion: Any) -> Mapping[str, object]:
        metadata: dict[str, object] = {}
        completion_id = getattr(completion, "id", None)
        created = getattr(completion, "created", None)
        if completion_id is not None:
            metadata["id"] = completion_id
        if created is not None:
            metadata["created"] = created
        return metadata

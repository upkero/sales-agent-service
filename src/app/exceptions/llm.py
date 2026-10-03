from src.app.exceptions.base import BaseAppException


class LLMError(BaseAppException):
    """Base exception for LLM client operations."""

    error_code = "llm_error"
    default_detail = "LLM operation failed."


class LLMInputError(LLMError, ValueError):
    """Raised when the LLM input payload is invalid."""

    status_code = 422
    error_code = "llm_input_error"


class LLMGenerationError(LLMError):
    """Raised when provider generation fails.

    A 502, not a 500: the provider failed, not this service, and the caller's
    best move (try again shortly) is the same as for any bad gateway. The code
    matches the sibling services'."""

    status_code = 502
    error_code = "generation_unavailable"
    default_detail = "The language model is unavailable."

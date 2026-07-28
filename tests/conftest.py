import os

# Set before importing anything from src: settings are read at import time and
# then cached. These are throwaway values so importing the container never fails
# on a required field; the tests inject fakes for the things that matter.
os.environ.setdefault("OPS_CORE_API_KEY", "test-ops-core-key-1234567890")
os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("LLM_MODEL", "stub-model")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("LOG_LEVEL", "WARNING")
# A small cap so the rate-limit test is cheap; well above what any other test sends.
os.environ.setdefault("TURN_RATE_LIMIT_PER_MINUTE", "5")

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402

from src.app.api.v1.middleware.rate_limit import reset_rate_limit  # noqa: E402
from src.app.core.settings.agent import SalesAgentSettings  # noqa: E402
from tests.fakes import FakePricingGateway  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    """The limiter's counters are process-global; keep cases independent."""
    reset_rate_limit()
    yield
    reset_rate_limit()


@pytest.fixture
def agent_settings() -> SalesAgentSettings:
    return SalesAgentSettings(name="Alex", company="Aurora Wellness", language="en")


@pytest.fixture
def pricing() -> FakePricingGateway:
    return FakePricingGateway()

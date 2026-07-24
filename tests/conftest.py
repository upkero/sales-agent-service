import os

# Set before importing anything from src: settings are read at import time and
# then cached. These are throwaway values so importing the container never fails
# on a required field; the tests inject fakes for the things that matter.
os.environ.setdefault("CORE_API_API_KEY", "test-core-api-key-1234567890")
os.environ.setdefault("LLM_PROVIDER", "ollama")
os.environ.setdefault("LLM_MODEL", "stub-model")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("LOG_LEVEL", "WARNING")

import pytest  # noqa: E402

from src.app.core.settings.agent import SalesAgentSettings  # noqa: E402
from tests.fakes import FakePricingGateway  # noqa: E402


@pytest.fixture
def agent_settings() -> SalesAgentSettings:
    return SalesAgentSettings(name="Alex", company="Aurora Wellness", language="en")


@pytest.fixture
def pricing() -> FakePricingGateway:
    return FakePricingGateway()

import pytest


@pytest.fixture(autouse=True)
def _reset_llm_rate_limit_state():
    """Cooldowns are process-wide by design; keep one test's failures from pausing providers in the next."""
    from agent.llm.ratelimit import COOLDOWNS

    COOLDOWNS.clear()
    yield
    COOLDOWNS.clear()

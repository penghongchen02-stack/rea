"""Tests exercise every bot's behaviour, including bots taken off (psb/retired.py):
put them back into the shared registry for each test, except tests marked
`running_bots`, which check what the engine actually runs."""
import pytest

from psb import registry


def pytest_configure(config):
    config.addinivalue_line("markers", "running_bots: use the registry without retired bots")


@pytest.fixture(autouse=True)
def _all_bots(request):
    if request.node.get_closest_marker("running_bots"):
        yield
        return
    saved = dict(registry.BOTS)
    registry.BOTS.update(registry.ALL_BOTS)
    yield
    registry.BOTS.clear()
    registry.BOTS.update(saved)

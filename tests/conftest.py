"""Test-suite guards against ever touching the real Todoist API.

Two independent fallbacks in the production code make an ambient
environment dangerous for tests: auth.resolve_token() falls back to
os.environ when no `environ=` is passed, and sync.SyncClient falls back to
the real urllib transport when no `transport=` is passed. A test that omits
both -- easy to do, since both are optional -- would otherwise pick up a
developer's real TODOIST_API_TOKEN and write to their real account.

The fixtures below are autouse so that failure mode is impossible: the
token is removed from the environment, and the real transport is replaced
by one that fails loudly the moment it is called.
"""

import pytest

from ticktick_to_todoist import sync

# Captured before the autouse fixture replaces the module attribute, so the
# few tests that need to exercise the genuine transport (with a stubbed
# urlopen) can still reach it -- see the real_urllib_transport fixture.
_REAL_URLLIB_TRANSPORT = sync._urllib_transport


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch):
    monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError(
            "test tried to make a real network call -- pass transport= "
            "explicitly"
        )

    monkeypatch.setattr(sync, "_urllib_transport", _boom)


@pytest.fixture
def real_urllib_transport():
    """The genuine urllib-backed transport, for tests that stub urlopen."""
    return _REAL_URLLIB_TRANSPORT

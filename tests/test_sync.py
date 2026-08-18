import json
import urllib.error
import urllib.request

import pytest

from fake_todoist import FakeTodoist
from ticktick_to_todoist import sync


def client(fake, **kwargs):
    return sync.SyncClient("tok", transport=fake.transport, pause=0.0, **kwargs)


def test_read_returns_the_requested_resources():
    fake = FakeTodoist()
    payload = client(fake).read(["user", "user_plan_limits"])
    assert payload["user"]["email"] == "someone@example.com"
    assert payload["user_plan_limits"]["current"]["plan_name"] == "pro"


def test_execute_returns_temp_id_mapping():
    fake = FakeTodoist()
    command = sync.new_command("project_add", {"name": "Buy"}, temp_id="p1")
    result = client(fake).execute([command])
    assert "p1" in result.temp_id_mapping
    assert result.errors == []
    assert list(fake.projects.values())[0]["name"] == "Buy"


def test_temp_ids_resolve_within_one_request():
    fake = FakeTodoist()
    commands = [
        sync.new_command("project_add", {"name": "Buy"}, temp_id="p1"),
        sync.new_command("item_add", {"content": "Milk", "project_id": "p1"},
                         temp_id="i1"),
    ]
    result = client(fake).execute(commands)
    project_id = result.temp_id_mapping["p1"]
    item_id = result.temp_id_mapping["i1"]
    assert fake.items[item_id]["project_id"] == project_id


def test_per_command_failures_are_collected_not_raised():
    fake = FakeTodoist(project_limit=1)
    commands = [
        sync.new_command("project_add", {"name": "A"}, temp_id="a"),
        sync.new_command("project_add", {"name": "B"}, temp_id="b"),
    ]
    result = client(fake).execute(commands)
    assert len(result.errors) == 1
    assert result.errors[0][0] == "project_add"


def test_a_rejection_without_an_error_key_still_carries_a_diagnostic():
    # A rejection dict is not guaranteed to have an "error" key. Reporting
    # the literal string "None" would destroy the only diagnostic the user
    # gets for that command.
    def transport(url, headers, body):
        payload = json.loads(body.decode("utf-8"))
        sync_status = {c["uuid"]: {"error_code": 42}
                       for c in payload["commands"]}
        return 200, {}, json.dumps({"sync_status": sync_status,
                                    "temp_id_mapping": {}}).encode("utf-8")

    api = sync.SyncClient("tok", transport=transport, pause=0.0)
    result = api.execute([sync.new_command("item_add", {"content": "x"})])
    message = result.errors[0][2]
    assert message != "None"
    assert "42" in message


def test_more_than_one_hundred_commands_is_rejected():
    fake = FakeTodoist()
    commands = [sync.new_command("item_add", {"content": str(i)})
                for i in range(101)]
    with pytest.raises(ValueError):
        client(fake).execute(commands)


def test_empty_command_list_makes_no_request():
    fake = FakeTodoist()
    result = client(fake).execute([])
    assert fake.requests == []
    assert result.temp_id_mapping == {}


def test_rate_limit_is_retried_after_waiting():
    fake = FakeTodoist()
    fake.fail_next_with(429)
    slept = []
    api = sync.SyncClient("tok", transport=fake.transport, pause=0.0,
                          sleep=slept.append)
    result = api.execute([sync.new_command("project_add", {"name": "Buy"},
                                           temp_id="p")])
    assert result.errors == []
    assert slept  # it waited before retrying


def test_server_error_raises_sync_error_without_leaking_the_token():
    fake = FakeTodoist()
    fake.fail_next_with(500)
    api = sync.SyncClient("supersecret", transport=fake.transport, pause=0.0,
                          sleep=lambda _: None, max_retries=0)
    with pytest.raises(sync.SyncError) as caught:
        api.execute([sync.new_command("project_add", {"name": "Buy"})])
    assert "supersecret" not in str(caught.value)


class _FakeResponse:
    status = 200
    headers = {}

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        return False


def test_a_network_level_failure_becomes_a_sync_error(monkeypatch,
                                                      real_urllib_transport):
    # urlopen raises URLError for DNS failures, refused connections and the
    # like. Left uncaught it escapes as a raw traceback from whichever call
    # happened to be in flight.
    def boom(_request, timeout=None):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(sync.SyncError) as caught:
        real_urllib_transport(sync.SYNC_URL,
                              {"Authorization": "Bearer supersecret"}, b"{}")
    assert "supersecret" not in str(caught.value)
    assert "Could not reach Todoist" in str(caught.value)


def test_a_socket_timeout_becomes_a_sync_error(monkeypatch,
                                               real_urllib_transport):
    def boom(_request, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(sync.SyncError):
        real_urllib_transport(sync.SYNC_URL, {}, b"{}")


def test_requests_are_made_with_a_timeout(monkeypatch, real_urllib_transport):
    seen = {}

    def fake_urlopen(_request, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    real_urllib_transport(sync.SYNC_URL, {}, b"{}")
    assert seen["timeout"] == sync.REQUEST_TIMEOUT_SECONDS


def test_each_command_gets_a_unique_uuid():
    a = sync.new_command("item_add", {"content": "x"})
    b = sync.new_command("item_add", {"content": "x"})
    assert a["uuid"] != b["uuid"]

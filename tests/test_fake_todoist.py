"""Regression tests for the fake Todoist transport itself.

Every later task (executor, CLI, end-to-end) builds on FakeTodoist as its
transport, so its command handling needs its own coverage independent of
whatever sync.py happens to exercise incidentally.
"""

from fake_todoist import FakeTodoist
from ticktick_to_todoist import sync


def client(fake, **kwargs):
    return sync.SyncClient("tok", transport=fake.transport, pause=0.0, **kwargs)


def test_section_add_creates_a_section_under_its_project():
    fake = FakeTodoist()
    commands = [
        sync.new_command("project_add", {"name": "Home"}, temp_id="p1"),
        sync.new_command("section_add", {"name": "Chores", "project_id": "p1"},
                         temp_id="s1"),
    ]
    result = client(fake).execute(commands)

    project_id = result.temp_id_mapping["p1"]
    section_id = result.temp_id_mapping["s1"]
    assert fake.sections[section_id]["name"] == "Chores"
    assert fake.sections[section_id]["project_id"] == project_id


def test_item_complete_marks_the_item_completed():
    fake = FakeTodoist()
    commands = [
        sync.new_command("item_add", {"content": "Buy milk"}, temp_id="i1"),
        sync.new_command("item_complete",
                         {"id": "i1", "date_completed": "2026-08-14T00:00:00Z"}),
    ]
    result = client(fake).execute(commands)

    item_id = result.temp_id_mapping["i1"]
    assert result.errors == []
    assert fake.completed[item_id] == "2026-08-14T00:00:00Z"


def test_item_complete_on_a_missing_item_is_a_per_command_error():
    fake = FakeTodoist()
    commands = [sync.new_command("item_complete", {"id": "9999"})]
    result = client(fake).execute(commands)

    assert len(result.errors) == 1
    assert result.errors[0][0] == "item_complete"


def test_project_delete_removes_the_project():
    fake = FakeTodoist()
    add_result = client(fake).execute(
        [sync.new_command("project_add", {"name": "Temp"}, temp_id="p1")])
    project_id = add_result.temp_id_mapping["p1"]

    delete_result = client(fake).execute(
        [sync.new_command("project_delete", {"id": project_id})])

    assert delete_result.errors == []
    assert project_id not in fake.projects
    assert project_id in fake.deleted


def test_item_delete_removes_the_item():
    fake = FakeTodoist()
    add_result = client(fake).execute(
        [sync.new_command("item_add", {"content": "Throwaway"}, temp_id="i1")])
    item_id = add_result.temp_id_mapping["i1"]

    delete_result = client(fake).execute(
        [sync.new_command("item_delete", {"id": item_id})])

    assert delete_result.errors == []
    assert item_id not in fake.items
    assert item_id in fake.deleted


def test_deleting_a_project_frees_a_limit_slot_for_a_new_project():
    fake = FakeTodoist(project_limit=2)
    api = client(fake)

    first = api.execute([sync.new_command("project_add", {"name": "A"},
                                          temp_id="a")])
    second = api.execute([sync.new_command("project_add", {"name": "B"},
                                           temp_id="b")])
    assert first.errors == []
    assert second.errors == []

    # Limit is now full: a third project should be rejected.
    blocked = api.execute([sync.new_command("project_add", {"name": "C"},
                                            temp_id="c")])
    assert len(blocked.errors) == 1
    assert blocked.errors[0][0] == "project_add"

    # Deleting one of the existing projects frees a slot.
    project_a_id = first.temp_id_mapping["a"]
    delete_result = api.execute(
        [sync.new_command("project_delete", {"id": project_a_id})])
    assert delete_result.errors == []

    retry = api.execute([sync.new_command("project_add", {"name": "D"},
                                          temp_id="d")])
    assert retry.errors == []
    assert "d" in retry.temp_id_mapping

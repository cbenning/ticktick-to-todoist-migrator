import json

from ticktick_to_todoist import state as state_mod


def test_new_state_starts_empty(tmp_path):
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    assert state.task_id("1") is None
    assert state.has_task("1") is False


def test_exists_reports_whether_a_run_is_in_progress(tmp_path):
    path = str(tmp_path / "s.json")
    assert state_mod.MigrationState.exists(path) is False
    state = state_mod.MigrationState.load(path)
    state.record_project("list:Buy", "p1")
    state.save()
    assert state_mod.MigrationState.exists(path) is True


def test_records_survive_a_save_and_reload(tmp_path):
    path = str(tmp_path / "s.json")
    state = state_mod.MigrationState.load(path)
    state.record_project("list:Buy", "p1")
    state.record_section("section:Buy", "s1")
    state.record_task("42", "i1", "p1")
    state.save()

    reloaded = state_mod.MigrationState.load(path)
    assert reloaded.project_id("list:Buy") == "p1"
    assert reloaded.section_id("section:Buy") == "s1"
    assert reloaded.task_id("42") == "i1"
    assert reloaded.has_task("42") is True


def test_state_file_never_contains_a_token(tmp_path):
    path = str(tmp_path / "s.json")
    state = state_mod.MigrationState.load(path)
    state.record_task("1", "i1", "p1")
    state.save()
    assert "token" not in json.loads(open(path).read())


def test_undo_deletes_created_projects_and_orphan_items(tmp_path):
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    state.record_project("list:Buy", "p1")
    state.record_task("1", "i1", "p1")     # inside a project we created
    state.record_task("2", "i2", "existing")  # inside a pre-existing project
    commands = state.undo_commands()
    kinds = [(c["type"], c["args"]["id"]) for c in commands]
    assert ("item_delete", "i2") in kinds
    assert ("project_delete", "p1") in kinds
    # Deleting the project removes its own items, so i1 is not deleted twice.
    assert ("item_delete", "i1") not in kinds


def test_undo_deletes_items_before_projects(tmp_path):
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    state.record_project("list:Buy", "p1")
    state.record_task("2", "i2", "existing")
    kinds = [c["type"] for c in state.undo_commands()]
    assert kinds.index("item_delete") < kinds.index("project_delete")


def test_clear_removes_the_file(tmp_path):
    path = str(tmp_path / "s.json")
    state = state_mod.MigrationState.load(path)
    state.record_project("list:Buy", "p1")
    state.save()
    state.clear()
    assert state_mod.MigrationState.exists(path) is False


def test_corrupt_state_file_is_reported_clearly(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    try:
        state_mod.MigrationState.load(str(path))
    except state_mod.StateError as error:
        assert "s.json" in str(error)
    else:
        raise AssertionError("expected StateError")


def test_newer_format_version_is_reported_clearly(tmp_path):
    path = tmp_path / "s.json"
    future = {
        "version": state_mod.FORMAT_VERSION + 1,
        "projects": {},
        "sections": {},
        "tasks": {},
    }
    path.write_text(json.dumps(future), encoding="utf-8")
    try:
        state_mod.MigrationState.load(str(path))
    except state_mod.StateError as error:
        assert "s.json" in str(error)
    else:
        raise AssertionError("expected StateError")

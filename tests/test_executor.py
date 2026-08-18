import json
import os

from fake_todoist import FakeTodoist
from ticktick_to_todoist import executor, layout, mapping, model, state as state_mod, sync

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def task(title="t", row_id="1", parent="", list_name="Buy",
         status=model.STATUS_NORMAL, completed_at=None, repeat="",
         converted=True, labels=(), due=None, description="", folder=""):
    return model.Task(
        row_id=row_id, parent_row_id=parent, folder=folder, list_name=list_name,
        title=title, description=description, labels=tuple(labels), priority=1,
        due=due, status=status, completed_at=completed_at, is_checklist=False,
        project_kind="TASK", repeat_raw=repeat, repeat_converted=converted,
        warnings=(),
    )


def run(tasks, tmp_path, mode=layout.LAYOUT_PROJECTS, **kwargs):
    fake = FakeTodoist()
    client = sync.SyncClient("tok", transport=fake.transport, pause=0.0)
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    plan = layout.build_plan(tasks, mode)
    result = executor.execute(plan, client, state, dry_run=False, **kwargs)
    return fake, state, result


def test_waves_put_parents_before_children():
    tasks = [task(row_id="2", parent="1"), task(row_id="1")]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)
    waves = executor.build_waves(list(plan.tasks))
    assert [t.task.row_id for t in waves[0]] == ["1"]
    assert [t.task.row_id for t in waves[1]] == ["2"]


def test_tasks_with_unresolvable_parents_land_in_the_first_wave():
    tasks = [task(row_id="1", parent="missing")]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)
    waves = executor.build_waves(list(plan.tasks))
    assert len(waves) == 1


def test_parent_child_cycle_terminates_instead_of_looping_forever():
    # Row 1's parent is row 2, and row 2's parent is row 1: neither can ever
    # be "placed" first. build_waves must still return, not hang.
    tasks = [task(row_id="1", parent="2"), task(row_id="2", parent="1")]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)
    waves = executor.build_waves(list(plan.tasks))
    all_ids = [t.task.row_id for wave in waves for t in wave]
    assert sorted(all_ids) == ["1", "2"]


def test_creates_projects_then_tasks(tmp_path):
    fake, _, result = run([task(title="Milk")], tmp_path)
    assert result.projects_created == 1
    assert result.tasks_created == 1
    assert list(fake.items.values())[0]["content"] == "Milk"


def test_sections_mode_creates_sections_and_assigns_them(tmp_path):
    fake, _, _ = run([task(title="Milk", list_name="Buy")], tmp_path,
                     mode=layout.LAYOUT_SECTIONS)
    assert len(fake.sections) == 1
    section_id = list(fake.sections)[0]
    assert list(fake.items.values())[0]["section_id"] == section_id


def test_completed_tasks_are_closed_with_their_original_date(tmp_path):
    fake, _, result = run(
        [task(title="Done", status=model.STATUS_COMPLETED,
              completed_at="2026-02-13T15:05:51Z")], tmp_path)
    assert result.tasks_completed == 1
    assert list(fake.completed.values()) == ["2026-02-13T15:05:51Z"]


def test_subtasks_reference_their_parents_real_id(tmp_path):
    tasks = [task(title="Parent", row_id="1"),
             task(title="Child", row_id="2", parent="1")]
    fake, state, _ = run(tasks, tmp_path)
    parent_id = state.task_id("1")
    child = fake.items[state.task_id("2")]
    assert child["parent_id"] == parent_id


def test_an_item_add_is_never_split_from_its_item_complete(tmp_path):
    tasks = [task(title=str(i), row_id=str(i),
                  status=model.STATUS_COMPLETED,
                  completed_at="2026-01-01T00:00:00Z") for i in range(60)]
    fake, _, _ = run(tasks, tmp_path)
    for payload in fake.requests:
        commands = payload.get("commands", [])
        added = {c["temp_id"] for c in commands if c["type"] == "item_add"}
        for command in commands:
            if command["type"] == "item_complete":
                assert command["args"]["id"] in added


def test_requests_never_exceed_one_hundred_commands(tmp_path):
    tasks = [task(title=str(i), row_id=str(i)) for i in range(250)]
    fake, _, _ = run(tasks, tmp_path)
    for payload in fake.requests:
        assert len(payload.get("commands", [])) <= 100


def test_state_records_every_created_id(tmp_path):
    tasks = [task(title="a", row_id="1"), task(title="b", row_id="2")]
    _, state, _ = run(tasks, tmp_path)
    assert state.task_id("1") and state.task_id("2")
    assert state.project_id("list:\x00Buy")


def test_resume_skips_tasks_already_recorded(tmp_path):
    tasks = [task(title="a", row_id="1"), task(title="b", row_id="2")]
    fake = FakeTodoist()
    client = sync.SyncClient("tok", transport=fake.transport, pause=0.0)
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)

    executor.execute(plan, client, state, dry_run=False)
    first_count = len(fake.items)
    result = executor.execute(plan, client, state, dry_run=False)

    assert len(fake.items) == first_count
    assert result.tasks_created == 0
    assert result.skipped_existing == 2


def test_dry_run_makes_no_requests(tmp_path):
    fake = FakeTodoist()
    client = sync.SyncClient("tok", transport=fake.transport, pause=0.0)
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    plan = layout.build_plan([task()], layout.LAYOUT_PROJECTS)
    result = executor.execute(plan, client, state, dry_run=True)
    assert fake.requests == []
    assert result.tasks_created == 1


def test_dry_run_writes_no_state_file(tmp_path):
    path = tmp_path / "s.json"
    fake = FakeTodoist()
    client = sync.SyncClient("tok", transport=fake.transport, pause=0.0)
    state = state_mod.MigrationState.load(str(path))
    executor.execute(layout.build_plan([task()], layout.LAYOUT_PROJECTS),
                     client, state, dry_run=True)
    assert not path.exists()


def test_metadata_footer_records_an_unconverted_repeat_rule(tmp_path):
    tasks = [task(title="Water", repeat="RRULE:FREQ=WEEKLY;BYDAY=MO",
                  converted=False)]
    fake, _, _ = run(tasks, tmp_path)
    description = list(fake.items.values())[0]["description"]
    assert "FREQ=WEEKLY;BYDAY=MO" in description


def test_metadata_footer_is_omitted_when_nothing_is_unmappable(tmp_path):
    fake, _, _ = run([task(title="Plain")], tmp_path)
    assert "description" not in list(fake.items.values())[0]


def test_metadata_footer_can_be_disabled(tmp_path):
    tasks = [task(title="Water", repeat="RRULE:FREQ=WEEKLY;BYDAY=MO",
                  converted=False)]
    fake, _, _ = run(tasks, tmp_path, metadata_footer=False)
    assert "description" not in list(fake.items.values())[0]


def test_command_errors_are_collected_into_the_result(tmp_path):
    fake = FakeTodoist(project_limit=1)
    client = sync.SyncClient("tok", transport=fake.transport, pause=0.0)
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    tasks = [task(title="a", row_id="1", list_name="A"),
             task(title="b", row_id="2", list_name="B")]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)
    result = executor.execute(plan, client, state, dry_run=False)
    assert result.errors


def test_state_is_saved_after_each_project_so_a_crash_does_not_lose_progress(tmp_path):
    # Projects are created one HTTP request at a time (each may be a parent
    # the next one needs). If the process dies partway through -- here, on
    # the second project's request -- everything already created for real
    # on Todoist must already be durably recorded on disk. Otherwise a
    # resumed run would recreate it, duplicating real user data.
    fake = FakeTodoist()
    real_transport = fake.transport
    calls = {"n": 0}

    def flaky_transport(url, headers, body):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash mid-migration")
        return real_transport(url, headers, body)

    client = sync.SyncClient("tok", transport=flaky_transport, pause=0.0)
    path = tmp_path / "s.json"
    state = state_mod.MigrationState.load(str(path))
    tasks = [task(title="a", row_id="1", folder="F1", list_name="L1"),
             task(title="b", row_id="2", folder="F2", list_name="L2")]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)

    try:
        executor.execute(plan, client, state, dry_run=False)
    except RuntimeError:
        pass

    assert path.exists()
    reloaded = state_mod.MigrationState.load(str(path))
    assert reloaded.project_id("folder:F1")


def test_a_rejected_item_complete_is_not_counted_as_completed(tmp_path):
    # item_add and item_complete are two separate commands. If the add
    # succeeds but Todoist rejects the completion, the summary must not
    # claim the task was closed while the error list right below it says the
    # completion failed.
    fake = FakeTodoist()
    real_transport = fake.transport

    def rejecting_transport(url, headers, body):
        status, headers_out, raw = real_transport(url, headers, body)
        request = json.loads(body.decode("utf-8"))
        response = json.loads(raw.decode("utf-8"))
        for command in request.get("commands", []):
            if command["type"] == "item_complete":
                fake.completed.clear()
                response["sync_status"][command["uuid"]] = {
                    "error": "CANNOT_COMPLETE", "error_code": 1,
                }
        return status, headers_out, json.dumps(response).encode("utf-8")

    client = sync.SyncClient("tok", transport=rejecting_transport, pause=0.0)
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    tasks = [task(title="Done", row_id="1", status=model.STATUS_COMPLETED,
                  completed_at="2026-01-01T00:00:00Z")]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)

    result = executor.execute(plan, client, state, dry_run=False)

    assert result.tasks_created == 1
    assert result.tasks_completed == 0
    assert any(command_type == "item_complete"
               for command_type, _, _ in result.errors)


def test_a_project_orphaned_by_its_failed_parent_is_reported(tmp_path):
    # When a folder project fails to create, its list projects fall back to
    # top-level. Only the parent's own failure was recorded, leaving the
    # user to infer that the child was structurally affected.
    fake = FakeTodoist()
    real_transport = fake.transport

    def rejecting_parent_transport(url, headers, body):
        status, headers_out, raw = real_transport(url, headers, body)
        request = json.loads(body.decode("utf-8"))
        response = json.loads(raw.decode("utf-8"))
        for command in request.get("commands", []):
            if (command["type"] == "project_add"
                    and command["args"]["name"] == "F1"):
                rejected_id = response["temp_id_mapping"].pop(
                    command["temp_id"], None)
                fake.projects.pop(rejected_id, None)
                response["sync_status"][command["uuid"]] = {
                    "error": "PROJECT_LIMIT_REACHED", "error_code": 1,
                }
        return status, headers_out, json.dumps(response).encode("utf-8")

    client = sync.SyncClient("tok", transport=rejecting_parent_transport,
                             pause=0.0)
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    plan = layout.build_plan([task(title="a", row_id="1", folder="F1",
                                   list_name="L1")], layout.LAYOUT_PROJECTS)

    result = executor.execute(plan, client, state, dry_run=False)

    assert ("project_add", "L1",
            "parent project failed to create; created as top-level instead"
            ) in result.errors
    created = [v for v in fake.projects.values() if v["name"] == "L1"]
    assert created and not created[0]["parent_id"]


def test_a_rejected_item_add_is_not_counted_as_created(tmp_path):
    # Both tasks land in the same wave/batch. The transport wrapper below
    # rewrites the response so task "b"'s item_add is reported as rejected
    # by Todoist (its temp_id is dropped from temp_id_mapping and an error
    # is put in sync_status), the way a real rejection -- bad due date,
    # plan limits -- would look. tasks_created must reflect only what was
    # actually confirmed, matching what state records and what errors say
    # failed.
    fake = FakeTodoist()
    real_transport = fake.transport

    def rejecting_transport(url, headers, body):
        status, headers_out, raw = real_transport(url, headers, body)
        request = json.loads(body.decode("utf-8"))
        response = json.loads(raw.decode("utf-8"))
        for command in request.get("commands", []):
            if (command["type"] == "item_add"
                    and command["args"]["content"] == "b"):
                rejected_id = response["temp_id_mapping"].pop(
                    command["temp_id"], None)
                fake.items.pop(rejected_id, None)
                response["sync_status"][command["uuid"]] = {
                    "error": "INVALID_DUE_DATE", "error_code": 1,
                }
        return status, headers_out, json.dumps(response).encode("utf-8")

    client = sync.SyncClient("tok", transport=rejecting_transport, pause=0.0)
    state = state_mod.MigrationState.load(str(tmp_path / "s.json"))
    tasks = [task(title="a", row_id="1"), task(title="b", row_id="2")]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)

    result = executor.execute(plan, client, state, dry_run=False)

    assert result.tasks_created == 1
    assert not state.has_task("2")
    assert any(description == "b" for _, description, _ in result.errors)

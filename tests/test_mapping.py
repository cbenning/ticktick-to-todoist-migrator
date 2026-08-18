import os

from ticktick_to_todoist import mapping, model

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def row(**overrides):
    base = {
        "Folder Name": "", "List Name": "Inbox", "Title": "Thing", "Kind": "TEXT",
        "Tags": "", "Content": "", "Is Check list": "N", "Start Date": "",
        "Due Date": "", "Reminder": "", "Repeat": "", "Priority": "0",
        "Status": "0", "Created Time": "2026-01-01T00:00:00+0000",
        "Completed Time": "", "Order": "1", "Timezone": "America/Vancouver",
        "Is All Day": "false", "Is Floating": "false", "Column Name": "",
        "Column Order": "", "View Mode": "list", "taskId": "1",
        "parentId": "", "projectKind": "TASK",
    }
    base.update(overrides)
    return base


def test_priority_is_inverted_to_todoist_scale():
    assert mapping.task_from_row(row(Priority="5")).priority == 4
    assert mapping.task_from_row(row(Priority="3")).priority == 3
    assert mapping.task_from_row(row(Priority="1")).priority == 2
    assert mapping.task_from_row(row(Priority="0")).priority == 1


def test_unknown_priority_falls_back_to_normal():
    assert mapping.task_from_row(row(Priority="9")).priority == 1


def test_tags_split_on_comma_and_strip():
    assert mapping.task_from_row(row(Tags="a, b ,c")).labels == ("a", "b", "c")


def test_empty_tags_produce_no_labels():
    assert mapping.task_from_row(row(Tags="")).labels == ()


def test_all_day_due_date_has_no_time_component():
    task = mapping.task_from_row(
        row(**{"Due Date": "2026-09-01T17:00:00+0000", "Is All Day": "true"})
    )
    assert task.due == {"date": "2026-09-01"}


def test_timed_due_date_keeps_time_and_timezone():
    task = mapping.task_from_row(
        row(**{"Due Date": "2026-09-01T17:00:00+0000", "Is All Day": "false"})
    )
    assert task.due["date"] == "2026-09-01T17:00:00Z"
    assert task.due["timezone"] == "America/Vancouver"


def test_no_due_date_produces_none():
    assert mapping.task_from_row(row()).due is None


def test_simple_recurrence_converts_to_natural_language():
    task = mapping.task_from_row(
        row(**{"Due Date": "2026-09-01T17:00:00+0000", "Repeat": "RRULE:FREQ=WEEKLY"})
    )
    assert task.due["string"] == "every week"
    assert task.due["is_recurring"] is True
    assert task.repeat_converted is True


def test_interval_recurrence_converts_with_plural_unit():
    task = mapping.task_from_row(
        row(**{"Due Date": "2026-09-01T17:00:00+0000",
               "Repeat": "RRULE:FREQ=WEEKLY;INTERVAL=2"})
    )
    assert task.due["string"] == "every 2 weeks"
    assert task.repeat_converted is True


def test_interval_of_one_uses_singular_unit():
    task = mapping.task_from_row(
        row(**{"Due Date": "2026-09-01T17:00:00+0000",
               "Repeat": "RRULE:FREQ=DAILY;INTERVAL=1"})
    )
    assert task.due["string"] == "every day"


def test_complex_recurrence_is_not_guessed_at():
    task = mapping.task_from_row(
        row(**{"Due Date": "2026-09-01T17:00:00+0000",
               "Repeat": "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"})
    )
    assert "string" not in task.due
    assert task.repeat_converted is False
    assert task.repeat_raw == "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"


def test_recurrence_without_a_due_date_is_still_recorded_as_unconverted():
    task = mapping.task_from_row(row(Repeat="RRULE:FREQ=WEEKLY;BYDAY=MO"))
    assert task.due is None
    assert task.repeat_converted is False


def test_convertible_recurrence_without_due_date_is_flagged_for_review():
    task = mapping.task_from_row(row(Repeat="RRULE:FREQ=WEEKLY"))
    assert task.due is None
    assert task.repeat_converted is False
    assert any("no due date" in w for w in task.warnings)


def test_status_values_are_named():
    assert mapping.task_from_row(row(Status="0")).status == model.STATUS_NORMAL
    assert mapping.task_from_row(row(Status="2")).status == model.STATUS_COMPLETED
    assert mapping.task_from_row(row(Status="-1")).status == model.STATUS_ABANDONED


def test_completion_timestamp_is_converted_to_zulu():
    task = mapping.task_from_row(
        row(Status="2", **{"Completed Time": "2026-02-13T15:05:51+0000"})
    )
    assert task.completed_at == "2026-02-13T15:05:51Z"


def test_empty_title_becomes_untitled():
    assert mapping.task_from_row(row(Title="  ")).title == "(untitled)"


def test_overlong_title_is_flagged_but_not_truncated_here():
    task = mapping.task_from_row(row(Title="x" * 600))
    assert len(task.title) == 600
    assert any("title" in w for w in task.warnings)


def test_checklist_flag_is_read():
    assert mapping.task_from_row(row(**{"Is Check list": "Y"})).is_checklist is True
    assert mapping.task_from_row(row(**{"Is Check list": "N"})).is_checklist is False


def test_load_tasks_reads_the_edge_case_fixture():
    tasks = mapping.load_tasks(os.path.join(FIXTURES, "edge_cases.csv"))
    assert len(tasks) == 6
    ship = next(t for t in tasks if t.title == "Ship v2")
    assert ship.folder == "Work"
    assert ship.list_name == "Projects"
    assert ship.priority == 4
    assert ship.labels == ("urgent", "eng")
    children = [t for t in tasks if t.parent_row_id == "100"]
    assert len(children) == 2

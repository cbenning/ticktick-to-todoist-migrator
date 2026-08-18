from ticktick_to_todoist import executor, layout, limits, model, preflight, report


def task(title="t", row_id="1", list_name="Buy", status=model.STATUS_NORMAL,
         repeat="", converted=True):
    return model.Task(
        row_id=row_id, parent_row_id="", folder="", list_name=list_name,
        title=title, description="", labels=(), priority=1, due=None,
        status=status, completed_at=None, is_checklist=False,
        project_kind="TASK", repeat_raw=repeat, repeat_converted=converted,
        warnings=(),
    )


def test_plan_summary_lists_projects_and_counts():
    plan = layout.build_plan([task(list_name="Buy")], layout.LAYOUT_PROJECTS)
    text = report.render_plan_summary(plan, limits.Limits())
    assert "Buy" in text
    assert "1" in text


def test_plan_summary_marks_assumed_limits():
    plan = layout.build_plan([task()], layout.LAYOUT_PROJECTS)
    text = report.render_plan_summary(plan, limits.FREE_PLAN_ASSUMPTION)
    assert "assumed" in text.lower()


def test_issue_rendering_includes_the_message_and_every_resolution():
    issue = preflight.Issue(
        key="k", severity=preflight.SEVERITY_BLOCKER, message="Too many things",
        resolutions=(
            preflight.Resolution("a", "Do A", "explains A", recommended=True),
            preflight.Resolution("b", "Do B", "explains B"),
        ),
    )
    text = report.render_issue(issue)
    assert "Too many things" in text
    assert "Do A" in text and "Do B" in text
    assert "recommended" in text.lower()


def test_result_marks_a_dry_run_clearly():
    plan = layout.build_plan([task()], layout.LAYOUT_PROJECTS)
    text = report.render_result(plan, executor.ExecutionResult(), dry_run=True)
    assert "DRY RUN" in text
    assert "nothing was written" in text.lower()


def test_result_lists_tasks_needing_manual_recurrence_setup():
    tasks = [task(title="Water plants", repeat="RRULE:FREQ=WEEKLY;BYDAY=MO",
                  converted=False)]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)
    text = report.render_result(plan, executor.ExecutionResult(), dry_run=False)
    assert "Water plants" in text


def test_result_lists_command_errors():
    plan = layout.build_plan([task()], layout.LAYOUT_PROJECTS)
    result = executor.ExecutionResult(errors=[("item_add", "Milk", "LIMIT")])
    text = report.render_result(plan, result, dry_run=False)
    assert "Milk" in text and "LIMIT" in text


def test_result_says_so_when_there_were_no_errors():
    plan = layout.build_plan([task()], layout.LAYOUT_PROJECTS)
    text = report.render_result(plan, executor.ExecutionResult(), dry_run=False)
    assert "no errors" in text.lower()

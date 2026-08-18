from ticktick_to_todoist import layout, limits, model, preflight


def task(title="t", folder="", list_name="Inbox", row_id="1", parent="",
         status=model.STATUS_NORMAL, labels=(), repeat="", converted=True,
         checklist=False, description=""):
    return model.Task(
        row_id=row_id, parent_row_id=parent, folder=folder, list_name=list_name,
        title=title, description=description, labels=tuple(labels), priority=1,
        due=None, status=status, completed_at=None, is_checklist=checklist,
        project_kind="TASK", repeat_raw=repeat, repeat_converted=converted,
        warnings=(),
    )


def plan_from(tasks, mode=layout.LAYOUT_PROJECTS):
    return layout.build_plan(tasks, mode)


def issue_keys(issues):
    return {i.key for i in issues}


def find(issues, key):
    return next(i for i in issues if i.key == key)


def test_no_issues_on_a_small_clean_export():
    plan = plan_from([task(list_name="Buy")])
    assert preflight.check(plan, limits.Limits()) == []


def test_too_many_projects_is_a_blocker_recommending_sections():
    tasks = [task(row_id=str(i), list_name="L{0}".format(i)) for i in range(8)]
    issues = preflight.check(plan_from(tasks), limits.Limits(max_projects=5))
    issue = find(issues, "project_cap")
    assert issue.severity == preflight.SEVERITY_BLOCKER
    recommended = [r for r in issue.resolutions if r.recommended]
    assert len(recommended) == 1
    assert recommended[0].key == "use_sections"


def test_existing_projects_count_against_the_cap():
    tasks = [task(row_id=str(i), list_name="L{0}".format(i)) for i in range(4)]
    assert preflight.check(plan_from(tasks), limits.Limits(max_projects=5)) == []
    issues = preflight.check(plan_from(tasks), limits.Limits(max_projects=5),
                             existing_projects=("Work", "Home", "Errands"))
    assert "project_cap" in issue_keys(issues)


def test_a_project_name_already_in_the_account_is_flagged():
    tasks = [task(list_name="Buy")]
    issues = preflight.check(plan_from(tasks), limits.Limits(),
                             existing_projects=("Buy",))
    issue = find(issues, "duplicate_project")
    assert issue.severity == preflight.SEVERITY_WARNING
    assert "Buy" in issue.message


def test_duplicate_name_matching_ignores_case_and_padding():
    issues = preflight.check(plan_from([task(list_name="Buy")]),
                             limits.Limits(), existing_projects=("  buy ",))
    assert "duplicate_project" in issue_keys(issues)


def test_no_duplicate_issue_when_names_are_distinct():
    issues = preflight.check(plan_from([task(list_name="Buy")]),
                             limits.Limits(), existing_projects=("Work",))
    assert "duplicate_project" not in issue_keys(issues)


def test_applying_rename_suffixes_the_clashing_project():
    tasks = [task(list_name="Buy")]
    plan = plan_from(tasks)
    rebuilt = preflight.apply(plan, "duplicate_project", "rename",
                              existing_projects=("Buy",))
    assert rebuilt.projects[0].name == "Buy (imported)"


def test_over_three_hundred_active_tasks_in_one_project_is_flagged():
    tasks = [task(row_id=str(i), list_name="Buy") for i in range(301)]
    issues = preflight.check(plan_from(tasks), limits.Limits(max_projects=50))
    assert "tasks_per_project" in issue_keys(issues)


def test_completed_tasks_do_not_count_toward_the_task_cap():
    tasks = [task(row_id=str(i), list_name="Buy", status=model.STATUS_COMPLETED)
             for i in range(400)]
    issues = preflight.check(plan_from(tasks), limits.Limits(max_projects=50))
    assert "tasks_per_project" not in issue_keys(issues)


def test_over_twenty_sections_in_one_project_is_flagged():
    tasks = [task(row_id=str(i), folder="Work", list_name="L{0}".format(i))
             for i in range(21)]
    issues = preflight.check(plan_from(tasks, layout.LAYOUT_SECTIONS),
                             limits.Limits(max_projects=50))
    assert "sections_per_project" in issue_keys(issues)


def test_applying_overflow_to_sections_resolves_the_section_cap():
    tasks = [task(row_id=str(i), folder="Work", list_name="L{0}".format(i))
             for i in range(21)]
    plan = plan_from(tasks, layout.LAYOUT_SECTIONS)
    rebuilt = preflight.apply(plan, "sections_per_project", "overflow")

    issues = preflight.check(rebuilt, limits.Limits(max_projects=50))
    assert "sections_per_project" not in issue_keys(issues)

    section_projects = {s.key: s.project_key for s in rebuilt.sections}
    for planned in rebuilt.tasks:
        if planned.section_key:
            assert planned.project_key == section_projects[planned.section_key]


def test_applying_overflow_to_tasks_keeps_sections_atomic():
    # Section sizes deliberately don't line up with the 300 boundary, so a
    # flat task-by-task bucketing (ignoring sections) would split the third
    # section's tasks across two projects if it didn't account for sections.
    sizes = [("L0", 150), ("L1", 100), ("L2", 150)]
    tasks = []
    row = 0
    for list_name, count in sizes:
        for _ in range(count):
            tasks.append(task(row_id=str(row), folder="Work", list_name=list_name))
            row += 1
    plan = plan_from(tasks, layout.LAYOUT_SECTIONS)
    rebuilt = preflight.apply(plan, "tasks_per_project", "overflow")

    section_projects = {s.key: s.project_key for s in rebuilt.sections}
    projects_by_section = {}
    for planned in rebuilt.tasks:
        assert planned.project_key == section_projects[planned.section_key]
        projects_by_section.setdefault(planned.section_key, set()).add(
            planned.project_key)
    assert all(len(keys) == 1 for keys in projects_by_section.values())

    issues = preflight.check(rebuilt, limits.Limits(max_projects=50))
    assert "tasks_per_project" not in issue_keys(issues)


def test_applying_overflow_moves_completed_tasks_with_their_section():
    # L0 has 150 active tasks; L1 has 200 active + 5 completed. 150 + 200 =
    # 350 active total in one project, forcing L1 into a sibling project.
    # The completed L1 tasks aren't part of that math, but they still live
    # in section L1, so they must move with it.
    tasks = []
    row = 0
    for _ in range(150):
        tasks.append(task(row_id=str(row), folder="Work", list_name="L0"))
        row += 1
    for _ in range(200):
        tasks.append(task(row_id=str(row), folder="Work", list_name="L1"))
        row += 1
    for _ in range(5):
        tasks.append(task(row_id=str(row), folder="Work", list_name="L1",
                          status=model.STATUS_COMPLETED))
        row += 1
    plan = plan_from(tasks, layout.LAYOUT_SECTIONS)
    rebuilt = preflight.apply(plan, "tasks_per_project", "overflow")

    section_projects = {s.key: s.project_key for s in rebuilt.sections}
    l1_key = next(s.key for s in rebuilt.sections if s.name == "L1")
    l1_new_project = section_projects[l1_key]
    assert l1_new_project != "project:Work"  # confirms the section did move

    l1_tasks = [t for t in rebuilt.tasks if t.section_key == l1_key]
    assert len(l1_tasks) == 205
    assert all(t.project_key == l1_new_project for t in l1_tasks)

    completed = [t for t in l1_tasks if t.task.status == model.STATUS_COMPLETED]
    assert len(completed) == 5
    assert all(t.project_key == l1_new_project for t in completed)


def test_applying_overflow_uses_the_limits_it_was_checked_against():
    # The whole point of reading user_plan_limits off the live API is to
    # adapt when an account's numbers differ from the published defaults. If
    # apply() trimmed to a hardcoded 300 instead, the recommended resolution
    # would silently be a no-op here and the run would die claiming nothing
    # fits.
    plan_limits = limits.Limits(tasks_per_project=10, max_projects=50)
    tasks = [task(row_id=str(i), list_name="Buy") for i in range(25)]
    plan = plan_from(tasks)
    assert "tasks_per_project" in issue_keys(preflight.check(plan, plan_limits))

    rebuilt = preflight.apply(plan, "tasks_per_project", "overflow",
                              plan_limits=plan_limits)
    assert "tasks_per_project" not in issue_keys(
        preflight.check(rebuilt, plan_limits))
    assert len(rebuilt.projects) == 3  # 10 + 10 + 5


def test_applying_section_overflow_uses_the_limits_it_was_checked_against():
    plan_limits = limits.Limits(sections_per_project=3, max_projects=50)
    tasks = [task(row_id=str(i), folder="Work", list_name="L{0}".format(i))
             for i in range(7)]
    plan = plan_from(tasks, layout.LAYOUT_SECTIONS)
    assert "sections_per_project" in issue_keys(
        preflight.check(plan, plan_limits))

    rebuilt = preflight.apply(plan, "sections_per_project", "overflow",
                              plan_limits=plan_limits)
    assert "sections_per_project" not in issue_keys(
        preflight.check(rebuilt, plan_limits))


def test_applying_truncation_uses_the_limits_it_was_checked_against():
    plan_limits = limits.Limits(max_title=10, max_description=20,
                                labels_per_task=2)
    tasks = [task(title="x" * 30, description="y" * 40,
                  labels=["a", "b", "c", "d"])]
    plan = plan_from(tasks)
    rebuilt = preflight.apply(plan, "long_title", "truncate",
                              plan_limits=plan_limits)
    assert len(rebuilt.tasks[0].task.title) == 10

    rebuilt = preflight.apply(plan, "long_description", "truncate",
                              plan_limits=plan_limits)
    assert rebuilt.tasks[0].task.description.startswith("y")
    assert preflight.TRUNCATION_MARKER in rebuilt.tasks[0].task.description

    rebuilt = preflight.apply(plan, "labels_per_task", "drop_excess",
                              plan_limits=plan_limits)
    assert rebuilt.tasks[0].task.labels == ("a", "b")


def test_an_overflow_sibling_stays_under_the_original_folder_parent():
    # In projects layout the list's project hangs off its folder's project.
    # A sibling created for spilled tasks must hang off the same folder, or
    # spilling silently un-nests the overflow.
    plan_limits = limits.Limits(tasks_per_project=10, max_projects=50)
    tasks = [task(row_id=str(i), folder="Work", list_name="Buy")
             for i in range(25)]
    plan = plan_from(tasks)
    original = next(p for p in plan.projects if p.name == "Buy")
    assert original.parent_key == "folder:Work"

    rebuilt = preflight.apply(plan, "tasks_per_project", "overflow",
                              plan_limits=plan_limits)
    siblings = [p for p in rebuilt.projects if p.name.startswith("Buy (")]
    assert siblings
    assert all(p.parent_key == original.parent_key for p in siblings)


def test_a_section_overflow_sibling_stays_under_the_original_parent():
    plan_limits = limits.Limits(sections_per_project=3, max_projects=50)
    tasks = [task(row_id=str(i), folder="Work", list_name="L{0}".format(i))
             for i in range(7)]
    plan = plan_from(tasks, layout.LAYOUT_SECTIONS)
    # Sections layout gives the folder project no parent of its own, so
    # copying the parent must faithfully copy "no parent" too.
    rebuilt = preflight.apply(plan, "sections_per_project", "overflow",
                              plan_limits=plan_limits)
    siblings = [p for p in rebuilt.projects if p.name.startswith("Work (")]
    assert siblings
    assert all(p.parent_key is None for p in siblings)


def test_too_many_labels_on_one_task_is_flagged():
    heavy = task(labels=["l{0}".format(i) for i in range(101)])
    issues = preflight.check(plan_from([heavy]), limits.Limits())
    assert "labels_per_task" in issue_keys(issues)


def test_too_many_distinct_labels_is_flagged():
    tasks = [task(row_id=str(i), labels=["l{0}".format(i)]) for i in range(501)]
    issues = preflight.check(plan_from(tasks), limits.Limits(max_projects=50))
    assert "labels_per_account" in issue_keys(issues)


def test_long_title_is_flagged_with_truncate_recommended():
    issues = preflight.check(plan_from([task(title="x" * 501)]), limits.Limits())
    issue = find(issues, "long_title")
    assert [r for r in issue.resolutions if r.recommended][0].key == "truncate"


def test_long_description_is_flagged():
    issues = preflight.check(plan_from([task(description="x" * 16384)]),
                             limits.Limits())
    assert "long_description" in issue_keys(issues)


def test_unconverted_repeat_is_informational():
    tasks = [task(repeat="RRULE:FREQ=WEEKLY;BYDAY=MO", converted=False)]
    issue = find(preflight.check(plan_from(tasks), limits.Limits()),
                 "unconverted_repeat")
    assert issue.severity == preflight.SEVERITY_INFO


def test_checklist_rows_are_flagged_informationally():
    issues = preflight.check(plan_from([task(checklist=True)]), limits.Limits())
    assert find(issues, "checklist_rows").severity == preflight.SEVERITY_INFO


def test_abandoned_rows_are_flagged_with_skip_recommended():
    tasks = [task(status=model.STATUS_ABANDONED)]
    issue = find(preflight.check(plan_from(tasks), limits.Limits()),
                 "abandoned_rows")
    assert [r for r in issue.resolutions if r.recommended][0].key == "skip"


def test_dangling_parent_reference_is_flagged():
    tasks = [task(row_id="1", parent="999")]
    assert "broken_parent" in issue_keys(preflight.check(plan_from(tasks),
                                                          limits.Limits()))


def test_parent_cycle_is_flagged():
    tasks = [task(row_id="1", parent="2"), task(row_id="2", parent="1")]
    assert "broken_parent" in issue_keys(preflight.check(plan_from(tasks),
                                                          limits.Limits()))


def test_assumed_limits_are_flagged_when_there_is_no_token():
    issues = preflight.check(plan_from([task()]), limits.FREE_PLAN_ASSUMPTION,
                             token_present=False)
    assert "assumed_limits" in issue_keys(issues)


def test_applying_use_sections_returns_a_sections_plan():
    tasks = [task(row_id=str(i), list_name="L{0}".format(i)) for i in range(8)]
    plan = plan_from(tasks)
    rebuilt = preflight.apply(plan, "project_cap", "use_sections")
    assert rebuilt.layout == layout.LAYOUT_SECTIONS
    assert len(rebuilt.projects) == 1


def test_applying_skip_to_abandoned_rows_drops_them():
    tasks = [task(row_id="1"), task(row_id="2", status=model.STATUS_ABANDONED)]
    rebuilt = preflight.apply(plan_from(tasks), "abandoned_rows", "skip")
    assert [t.task.row_id for t in rebuilt.tasks] == ["1"]


def test_applying_truncate_shortens_the_title_and_keeps_the_original():
    long_title = "x" * 600
    tasks = [task(title=long_title)]
    rebuilt = preflight.apply(plan_from(tasks), "long_title", "truncate")
    result = rebuilt.tasks[0].task
    assert len(result.title) == 500
    assert long_title in result.description


def test_applying_drop_excess_labels_trims_to_the_cap():
    tasks = [task(labels=["l{0}".format(i) for i in range(120)])]
    rebuilt = preflight.apply(plan_from(tasks), "labels_per_task",
                              "drop_excess")
    assert len(rebuilt.tasks[0].task.labels) == 100


def test_proceed_is_always_a_no_op():
    plan = plan_from([task()])
    assert preflight.apply(plan, "abandoned_rows", "proceed") is plan


def test_check_is_pure_and_does_not_mutate_the_plan():
    tasks = [task(row_id=str(i), list_name="L{0}".format(i)) for i in range(8)]
    plan = plan_from(tasks)
    before = plan.projects
    preflight.check(plan, limits.Limits(max_projects=5))
    assert plan.projects is before

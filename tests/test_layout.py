from ticktick_to_todoist import layout, model


def task(title, folder="", list_name="Inbox", row_id="1",
         status=model.STATUS_NORMAL, project_kind="TASK"):
    return model.Task(
        row_id=row_id, parent_row_id="", folder=folder, list_name=list_name,
        title=title, description="", labels=(), priority=1, due=None,
        status=status, completed_at=None, is_checklist=False,
        project_kind=project_kind, repeat_raw="", repeat_converted=False,
        warnings=(),
    )


def test_projects_mode_nests_lists_under_their_folder():
    tasks = [task("a", folder="Work", list_name="Projects", row_id="1"),
             task("b", folder="Work", list_name="Recurring", row_id="2")]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)
    names = {p.name: p for p in plan.projects}
    assert set(names) == {"Work", "Projects", "Recurring"}
    assert names["Work"].parent_key is None
    assert names["Projects"].parent_key == names["Work"].key
    assert plan.sections == ()


def test_projects_mode_leaves_folderless_lists_at_top_level():
    plan = layout.build_plan([task("a", list_name="Buy")], layout.LAYOUT_PROJECTS)
    assert [p.name for p in plan.projects] == ["Buy"]
    assert plan.projects[0].parent_key is None


def test_sections_mode_turns_lists_into_sections_of_the_folder_project():
    tasks = [task("a", folder="Work", list_name="Projects", row_id="1"),
             task("b", folder="Work", list_name="Recurring", row_id="2")]
    plan = layout.build_plan(tasks, layout.LAYOUT_SECTIONS)
    assert [p.name for p in plan.projects] == ["Work"]
    assert sorted(s.name for s in plan.sections) == ["Projects", "Recurring"]
    assert all(s.project_key == plan.projects[0].key for s in plan.sections)


def test_sections_mode_gathers_folderless_lists_into_one_project():
    tasks = [task("a", list_name="Buy", row_id="1"),
             task("b", list_name="Chores", row_id="2")]
    plan = layout.build_plan(tasks, layout.LAYOUT_SECTIONS)
    assert [p.name for p in plan.projects] == ["Imported"]
    assert sorted(s.name for s in plan.sections) == ["Buy", "Chores"]


def test_every_task_is_assigned_to_a_project():
    tasks = [task("a", folder="Work", list_name="Projects", row_id="1"),
             task("b", list_name="Buy", row_id="2")]
    for mode in (layout.LAYOUT_PROJECTS, layout.LAYOUT_SECTIONS):
        plan = layout.build_plan(tasks, mode)
        keys = {p.key for p in plan.projects}
        assert len(plan.tasks) == 2
        assert all(t.project_key in keys for t in plan.tasks)


def test_sections_mode_assigns_each_task_to_its_list_section():
    plan = layout.build_plan([task("a", list_name="Buy")], layout.LAYOUT_SECTIONS)
    section = plan.sections[0]
    assert plan.tasks[0].section_key == section.key


def test_projects_mode_assigns_no_sections():
    plan = layout.build_plan([task("a", list_name="Buy")], layout.LAYOUT_PROJECTS)
    assert plan.tasks[0].section_key is None


def test_project_order_is_stable_and_parents_come_first():
    tasks = [task("a", folder="Work", list_name="Projects", row_id="1")]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)
    assert plan.projects[0].name == "Work"


def test_task_counts_exclude_completed_tasks():
    tasks = [task("a", list_name="Buy", row_id="1"),
             task("b", list_name="Buy", row_id="2", status=model.STATUS_COMPLETED)]
    plan = layout.build_plan(tasks, layout.LAYOUT_PROJECTS)
    counts = layout.project_task_counts(plan)
    assert list(counts.values()) == [1]

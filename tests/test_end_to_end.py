import os

from fake_todoist import FakeTodoist
from ticktick_to_todoist import cli

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
OVER_LIMIT = os.path.join(FIXTURES, "over_limit.csv")
EDGE = os.path.join(FIXTURES, "edge_cases.csv")


def test_free_plan_over_limit_export_switches_to_sections(tmp_path, capsys):
    fake = FakeTodoist(project_limit=5, plan_name="free")
    code = cli.main(["--csv", OVER_LIMIT, "--live", "--yes",
                     "--state-file", str(tmp_path / "s.json")],
                    transport=fake.transport,
                    environ={"TODOIST_API_TOKEN": "tok"})
    assert code == 0
    assert len(fake.projects) == 1
    assert len(fake.sections) == 12
    assert len(fake.items) == 12


def test_edge_case_export_produces_the_expected_todoist_shape(tmp_path):
    fake = FakeTodoist()
    cli.main(["--csv", EDGE, "--live", "--yes", "--layout", "projects",
              "--state-file", str(tmp_path / "s.json")],
             transport=fake.transport, environ={"TODOIST_API_TOKEN": "tok"})

    # "Abandoned Test" holds only the abandoned "Old idea" row, and the
    # default (recommended, non-interactive) resolution for abandoned rows
    # is to leave them out entirely. With no surviving task assigned to it,
    # the plan never creates a project for that list -- correctly, since an
    # empty project would misrepresent a row that was deliberately excluded.
    names = sorted(p["name"] for p in fake.projects.values())
    assert names == ["Projects", "Recurring", "Work"]

    # The Work folder is a parent of both of its lists.
    work_id = next(k for k, v in fake.projects.items() if v["name"] == "Work")
    children = [v["name"] for v in fake.projects.values()
                if v.get("parent_id") == work_id]
    assert sorted(children) == ["Projects", "Recurring"]

    titles = {i["content"] for i in fake.items.values()}
    assert "Ship v2" in titles
    assert "Old idea" not in titles          # abandoned, skipped by default

    # The completed subtask was closed with its original date.
    notify = next(k for k, v in fake.items.items()
                  if v["content"] == "Notify legal")
    assert fake.completed[notify] == "2026-01-05T00:00:00Z"

    # The subtask points at its parent.
    ship = next(k for k, v in fake.items.items() if v["content"] == "Ship v2")
    doc = next(k for k, v in fake.items.items()
               if v["content"] == "Write launch doc")
    assert fake.items[doc]["parent_id"] == ship

    # The simple recurrence converted; the complex one did not.
    water = next(v for v in fake.items.values() if v["content"] == "Water plants")
    assert water["due"]["string"] == "every 2 weeks"
    complicated = next(v for v in fake.items.values()
                       if v["content"] == "Complicated schedule")
    assert "string" not in complicated["due"]
    assert "BYDAY=MO,WE,FR" in complicated["description"]


def test_a_failed_run_can_be_resumed_without_duplicating(tmp_path):
    fake = FakeTodoist()
    state_file = str(tmp_path / "s.json")
    args = ["--csv", EDGE, "--live", "--yes", "--layout", "projects",
            "--state-file", state_file]
    cli.main(args, transport=fake.transport,
             environ={"TODOIST_API_TOKEN": "tok"})
    first = dict(fake.items)

    cli.main(args + ["--resume"], transport=fake.transport,
             environ={"TODOIST_API_TOKEN": "tok"})
    assert fake.items == first


def test_undo_leaves_the_account_as_it_was(tmp_path):
    fake = FakeTodoist()
    state_file = str(tmp_path / "s.json")
    cli.main(["--csv", EDGE, "--live", "--yes", "--layout", "projects",
              "--state-file", state_file],
             transport=fake.transport, environ={"TODOIST_API_TOKEN": "tok"})
    cli.main(["--undo", "--yes", "--state-file", state_file],
             transport=fake.transport, environ={"TODOIST_API_TOKEN": "tok"})
    assert fake.projects == {}


def test_dry_run_then_live_produce_the_same_task_count(tmp_path, capsys):
    cli.main(["--csv", EDGE, "--yes", "--layout", "projects"])
    dry_output = capsys.readouterr().out

    fake = FakeTodoist()
    cli.main(["--csv", EDGE, "--live", "--yes", "--layout", "projects",
              "--state-file", str(tmp_path / "s.json")],
             transport=fake.transport, environ={"TODOIST_API_TOKEN": "tok"})
    live_output = capsys.readouterr().out

    def created(text):
        line = [l for l in text.splitlines() if "Tasks created:" in l][0]
        return int(line.split(":")[1].strip())

    assert created(dry_output) == created(live_output)

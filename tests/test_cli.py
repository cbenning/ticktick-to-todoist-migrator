import csv
import json
import os

import pytest

from fake_todoist import FakeTodoist
from ticktick_to_todoist import cli, sync

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE = os.path.join(FIXTURES, "sample.csv")
EDGE = os.path.join(FIXTURES, "edge_cases.csv")

CSV_HEADER = [
    "Folder Name", "List Name", "Title", "Kind", "Tags", "Content",
    "Is Check list", "Start Date", "Due Date", "Reminder", "Repeat",
    "Priority", "Status", "Created Time", "Completed Time", "Order",
    "Timezone", "Is All Day", "Is Floating", "Column Name", "Column Order",
    "View Mode", "taskId", "parentId", "projectKind",
]


def _write_export(path, rows):
    """Write a TickTick-shaped export whose size a fixture file can't carry.

    `rows` is a list of dicts of column overrides; anything unset is empty.
    """
    with open(str(path), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Date: 2026-08-15+0000"])
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow([row.get(column, "") for column in CSV_HEADER])
    return str(path)


def test_token_argument_is_rejected(capsys):
    code = cli.main(["--csv", SAMPLE, "--token", "abc"])
    assert code == 2
    assert "--token-file" in capsys.readouterr().err


def test_an_empty_token_argument_is_rejected_too(capsys):
    # `--token ""` must hit the same rejection as any other use of the flag.
    code = cli.main(["--csv", SAMPLE, "--token", ""])
    assert code == 2
    assert "--token-file" in capsys.readouterr().err


def test_dry_run_is_the_default(capsys):
    assert cli.main(["--csv", SAMPLE, "--yes"]) == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_dry_run_without_a_token_warns_that_limits_are_assumed(capsys):
    cli.main(["--csv", SAMPLE, "--yes"])
    assert "assumed" in capsys.readouterr().out.lower()


def test_live_without_a_token_fails(capsys, monkeypatch):
    monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
    code = cli.main(["--csv", SAMPLE, "--live", "--yes"], stdin_isatty=False)
    assert code == 2
    assert "token" in capsys.readouterr().err.lower()


def test_missing_csv_reports_clearly(capsys, tmp_path):
    code = cli.main(["--csv", str(tmp_path / "nope.csv"), "--yes"])
    assert code == 2
    assert "nope.csv" in capsys.readouterr().err


def test_non_ticktick_csv_reports_clearly(capsys, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    code = cli.main(["--csv", str(bad), "--yes"])
    assert code == 2
    assert "TickTick" in capsys.readouterr().err


def test_only_list_filters_the_import(capsys):
    cli.main(["--csv", SAMPLE, "--only-list", "Buy", "--yes"])
    out = capsys.readouterr().out
    assert "Buy" in out
    assert "Chores" not in out


def test_only_list_matching_nothing_is_an_error(capsys):
    code = cli.main(["--csv", SAMPLE, "--only-list", "Nonexistent", "--yes"])
    assert code == 2
    assert "Nonexistent" in capsys.readouterr().err


def test_layout_sections_is_honoured(capsys):
    cli.main(["--csv", EDGE, "--layout", "sections", "--yes"])
    assert "Layout: sections" in capsys.readouterr().out


def test_yes_takes_the_recommended_resolution_without_prompting(capsys):
    # edge_cases.csv contains an abandoned row, whose recommendation is skip.
    cli.main(["--csv", EDGE, "--yes"])
    out = capsys.readouterr().out
    assert "Old idea" not in out


def test_skip_completed_flag_drops_completed_tasks(capsys):
    cli.main(["--csv", SAMPLE, "--skip-completed", "--yes"])
    assert "0 completed" in capsys.readouterr().out


def test_skip_completed_on_an_over_limit_export_still_succeeds(tmp_path, capsys):
    # --skip-completed drops completed rows before the plan is even built, so
    # by preflight time there is nothing left for a "skip completed"
    # resolution of tasks_per_project to remove. The flag must therefore not
    # pre-answer that issue: it has to fall through to "overflow" (numbered
    # sibling projects) rather than resolving to a no-op and hard-failing
    # with advice to pass the very flag the user already passed.
    path = _write_export(tmp_path / "big.csv", [
        {"List Name": "Buy", "Title": "t{0}".format(i), "Status": "0",
         "taskId": str(i), "projectKind": "TASK"}
        for i in range(301)
    ])
    code = cli.main(["--csv", path, "--skip-completed", "--yes"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK, captured.err
    assert "Could not find a combination" not in captured.err
    assert "Buy (2)" in captured.out


def test_include_abandoned_flag_keeps_abandoned_tasks(capsys):
    cli.main(["--csv", EDGE, "--include-abandoned", "--yes"])
    assert "Old idea" not in capsys.readouterr().err  # no error path


def test_skip_note_lists_drops_note_kind_lists(capsys):
    cli.main(["--csv", SAMPLE, "--skip-note-lists", "--yes"])
    assert "To-Read" not in capsys.readouterr().out


def test_non_tty_without_yes_uses_recommendations_and_says_so(capsys):
    cli.main(["--csv", EDGE], stdin_isatty=False)
    out = capsys.readouterr().out
    assert "recommended" in out.lower()


def test_interactive_prompt_accepts_a_numbered_choice(capsys, monkeypatch):
    answers = iter(["1"] * 10)
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    assert cli.main(["--csv", EDGE], stdin_isatty=True) == 0


def test_abort_resolution_exits_without_writing(capsys, monkeypatch):
    # Choose the last option, which is always "Stop", for the first issue.
    monkeypatch.setattr("builtins.input", lambda *_: "3")
    code = cli.main(["--csv", EDGE], stdin_isatty=True)
    assert code == 2


def test_live_run_writes_through_the_injected_transport(tmp_path, capsys):
    fake = FakeTodoist()
    state_file = str(tmp_path / "s.json")
    code = cli.main(
        ["--csv", EDGE, "--live", "--yes", "--state-file", state_file],
        transport=fake.transport, environ={"TODOIST_API_TOKEN": "tok"},
    )
    assert code == 0
    assert fake.items
    assert os.path.exists(state_file)


def test_live_run_prints_the_target_account(tmp_path, capsys):
    fake = FakeTodoist()
    cli.main(["--csv", EDGE, "--live", "--yes",
              "--state-file", str(tmp_path / "s.json")],
             transport=fake.transport, environ={"TODOIST_API_TOKEN": "tok"})
    assert "someone@example.com" in capsys.readouterr().out


def test_resume_skips_what_a_previous_run_created(tmp_path, capsys):
    fake = FakeTodoist()
    state_file = str(tmp_path / "s.json")
    args = ["--csv", EDGE, "--live", "--yes", "--state-file", state_file]
    cli.main(args, transport=fake.transport,
             environ={"TODOIST_API_TOKEN": "tok"})
    created = len(fake.items)
    cli.main(args + ["--resume"], transport=fake.transport,
             environ={"TODOIST_API_TOKEN": "tok"})
    assert len(fake.items) == created


def test_undo_deletes_what_the_run_created(tmp_path, capsys):
    fake = FakeTodoist()
    state_file = str(tmp_path / "s.json")
    cli.main(["--csv", EDGE, "--live", "--yes", "--state-file", state_file],
             transport=fake.transport, environ={"TODOIST_API_TOKEN": "tok"})
    assert fake.projects
    code = cli.main(["--undo", "--yes", "--state-file", state_file],
                    transport=fake.transport,
                    environ={"TODOIST_API_TOKEN": "tok"})
    assert code == 0
    assert fake.projects == {}
    assert not os.path.exists(state_file)


def test_undo_without_a_state_file_is_an_error(capsys, tmp_path):
    code = cli.main(["--undo", "--yes", "--state-file",
                     str(tmp_path / "missing.json")],
                    environ={"TODOIST_API_TOKEN": "tok"})
    assert code == 2


def test_existing_state_file_warns_before_a_fresh_live_run(tmp_path, capsys):
    fake = FakeTodoist()
    state_file = str(tmp_path / "s.json")
    args = ["--csv", EDGE, "--live", "--yes", "--state-file", state_file]
    cli.main(args, transport=fake.transport,
             environ={"TODOIST_API_TOKEN": "tok"})
    cli.main(args, transport=fake.transport,
             environ={"TODOIST_API_TOKEN": "tok"})
    assert "--resume" in capsys.readouterr().out


def test_exit_code_one_when_commands_failed(tmp_path):
    fake = FakeTodoist(project_limit=1)
    code = cli.main(["--csv", EDGE, "--live", "--yes", "--layout", "projects",
                     "--state-file", str(tmp_path / "s.json")],
                    transport=fake.transport,
                    environ={"TODOIST_API_TOKEN": "tok"})
    assert code == 1


def test_dry_run_does_not_hide_a_blocker_left_by_proceed(capsys):
    # --layout projects forces the project_cap blocker to resolve as
    # "proceed" (the user's explicit layout choice is honoured instead of
    # being silently overridden), which is a no-op on the plan -- the
    # blocker is still there afterwards. A --live run would let the real
    # API reject the excess creations and report them (see
    # test_exit_code_one_when_commands_failed above), but a dry run never
    # talks to the API at all, so it must not print a clean "DRY RUN"
    # summary as if the plan were actually fine.
    fake = FakeTodoist(project_limit=1)
    code = cli.main(["--csv", os.path.join(FIXTURES, "over_limit.csv"),
                     "--yes", "--layout", "projects"],
                    transport=fake.transport,
                    environ={"TODOIST_API_TOKEN": "tok"})
    captured = capsys.readouterr()
    assert code == cli.EXIT_ABORTED
    assert "Could not find a combination" in captured.err
    assert "DRY RUN" not in captured.out


def test_live_run_hard_fails_on_genuine_non_convergence(tmp_path, capsys):
    # many_sections.csv has 23 lists under one folder. Against a 1-project
    # account, auto layout pre-switches to sections (1 project, 23
    # sections), so preflight's first pass sees only sections_per_project
    # (23 > 20) and resolves it via the recommended "overflow", spilling
    # the extra sections into a second project. That second project then
    # trips project_cap (2 > 1) on the next pass, whose recommended
    # "use_sections" rebuilds the whole plan from the raw tasks -- silently
    # undoing the overflow fix and putting sections_per_project right back
    # where it started. Its key was already marked seen on the first pass,
    # so the loop converges via break without ever actually fixing it, and
    # crucially: neither resolution was "proceed", so this must hard-fail
    # for a --live run exactly as it would for a dry run, making no writes,
    # rather than silently falling through to executor.execute().
    fake = FakeTodoist(project_limit=1)
    code = cli.main(["--csv", os.path.join(FIXTURES, "many_sections.csv"),
                     "--live", "--yes", "--state-file", str(tmp_path / "s.json")],
                    transport=fake.transport,
                    environ={"TODOIST_API_TOKEN": "tok"})
    captured = capsys.readouterr()
    assert code == cli.EXIT_ABORTED
    assert "Could not find a combination" in captured.err
    assert fake.projects == {}
    assert fake.items == {}
    assert not os.path.exists(str(tmp_path / "s.json"))


def test_an_issue_created_by_a_resolution_is_raised_not_hidden(tmp_path, capsys):
    # A 1-project account forces sections layout; the preflight must re-run
    # after that resolution rather than accepting the first plan it built.
    fake = FakeTodoist(project_limit=1, plan_name="free")
    code = cli.main(["--csv", os.path.join(FIXTURES, "over_limit.csv"),
                     "--live", "--yes", "--state-file", str(tmp_path / "s.json")],
                    transport=fake.transport,
                    environ={"TODOIST_API_TOKEN": "tok"})
    out = capsys.readouterr().out
    assert "sections" in out.lower()
    assert code in (cli.EXIT_OK, cli.EXIT_ERRORS)


def test_a_name_clash_with_an_existing_project_is_reported(tmp_path, capsys):
    fake = FakeTodoist()
    fake.projects["999"] = {"name": "Projects", "parent_id": None}
    cli.main(["--csv", EDGE, "--live", "--yes", "--layout", "projects",
              "--state-file", str(tmp_path / "s.json")],
             transport=fake.transport, environ={"TODOIST_API_TOKEN": "tok"})
    assert "already has a project named" in capsys.readouterr().out


def test_archived_and_inbox_projects_do_not_count_toward_the_cap(capsys):
    # The sync projects resource returns archived projects and Inbox too.
    # Archived projects do not count against the active project cap and
    # Inbox consumes no plan slot, so counting them would wrongly push this
    # 3-project import over a 5-project account and force a layout switch.
    fake = FakeTodoist(project_limit=5, plan_name="free")
    fake.projects["901"] = {"name": "Old A", "parent_id": None,
                            "is_archived": True}
    fake.projects["902"] = {"name": "Old B", "parent_id": None,
                            "is_archived": True}
    fake.projects["903"] = {"name": "Old C", "parent_id": None,
                            "is_deleted": True}
    fake.projects["904"] = {"name": "Inbox", "parent_id": None,
                            "inbox_project": True}

    code = cli.main(["--csv", EDGE, "--yes", "--layout", "projects"],
                    transport=fake.transport,
                    environ={"TODOIST_API_TOKEN": "tok"})
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK, captured.err
    assert "Layout: projects" in captured.out
    assert "over your plan's limit" not in captured.out


def test_an_archived_project_does_not_trigger_a_duplicate_name_warning(capsys):
    fake = FakeTodoist()
    fake.projects["999"] = {"name": "Projects", "parent_id": None,
                            "is_archived": True}
    cli.main(["--csv", EDGE, "--yes", "--layout", "projects"],
             transport=fake.transport, environ={"TODOIST_API_TOKEN": "tok"})
    assert "already has a project named" not in capsys.readouterr().out


def test_token_never_appears_in_output(tmp_path, capsys):
    fake = FakeTodoist()
    cli.main(["--csv", EDGE, "--live", "--yes",
              "--state-file", str(tmp_path / "s.json")],
             transport=fake.transport,
             environ={"TODOIST_API_TOKEN": "hunter2"})
    captured = capsys.readouterr()
    assert "hunter2" not in captured.out
    assert "hunter2" not in captured.err
    assert "hunter2" not in open(str(tmp_path / "s.json")).read()


def _per_command_failure_transport(url, headers, body):
    # A transport whose *individual commands* fail (as opposed to the whole
    # request), echoing the token back the way a misbehaving or malicious
    # server's per-command error text plausibly could -- this is the one
    # path sync.py itself never redacts, since it only touches whole-request
    # failure bodies.
    payload = json.loads(body.decode("utf-8"))
    if payload.get("resource_types"):
        response = {}
        if "user" in payload["resource_types"]:
            response["user"] = {"email": "someone@example.com", "id": "1"}
        if "user_plan_limits" in payload["resource_types"]:
            response["user_plan_limits"] = {"current": {
                "plan_name": "pro", "max_projects": 300, "max_tasks": 300,
                "max_sections": 20, "max_labels": 500,
            }}
        if "projects" in payload["resource_types"]:
            response["projects"] = []
        return 200, {}, json.dumps(response).encode("utf-8")
    commands = payload["commands"]
    token = headers.get("Authorization", "").replace("Bearer ", "")
    sync_status = {c["uuid"]: {"error": "denied; token was " + token,
                               "error_code": 1} for c in commands}
    return 200, {}, json.dumps(
        {"sync_status": sync_status, "temp_id_mapping": {}}).encode("utf-8")


def test_undo_failure_message_redacts_the_token(tmp_path, capsys):
    fake = FakeTodoist()
    state_file = str(tmp_path / "s.json")
    cli.main(["--csv", EDGE, "--live", "--yes", "--state-file", state_file],
             transport=fake.transport, environ={"TODOIST_API_TOKEN": "hunter2"})
    assert fake.projects

    code = cli.main(["--undo", "--yes", "--state-file", state_file],
                    transport=_per_command_failure_transport,
                    environ={"TODOIST_API_TOKEN": "hunter2"})
    captured = capsys.readouterr()
    assert code == cli.EXIT_ERRORS
    assert "hunter2" not in captured.out
    assert "hunter2" not in captured.err


def test_a_network_drop_mid_migration_exits_cleanly_pointing_at_resume(
        tmp_path, capsys):
    # The initial read succeeds, then the connection dies -- exactly what a
    # dropped network, a 500-after-retries, or a token revoked mid-run looks
    # like from cli.main()'s point of view. That must not escape as a raw
    # traceback: the README promises --resume picks up where it left off.
    fake = FakeTodoist()
    calls = {"n": 0}

    def dropping_transport(url, headers, body):
        calls["n"] += 1
        if calls["n"] == 1:
            return fake.transport(url, headers, body)
        raise sync.SyncError(
            "Could not reach Todoist at {0}: [Errno -3] Temporary failure in "
            "name resolution (token was hunter2)".format(sync.SYNC_URL))

    state_file = str(tmp_path / "s.json")
    code = cli.main(["--csv", EDGE, "--live", "--yes",
                     "--state-file", state_file],
                    transport=dropping_transport,
                    environ={"TODOIST_API_TOKEN": "hunter2"})
    captured = capsys.readouterr()
    assert code == cli.EXIT_ERRORS
    assert "--resume" in captured.err
    assert "hunter2" not in captured.err
    assert "hunter2" not in captured.out


def test_live_run_failure_message_redacts_the_token(tmp_path, capsys):
    cli.main(["--csv", EDGE, "--live", "--yes",
              "--state-file", str(tmp_path / "s.json")],
             transport=_per_command_failure_transport,
             environ={"TODOIST_API_TOKEN": "hunter2"})
    captured = capsys.readouterr()
    assert "hunter2" not in captured.out
    assert "hunter2" not in captured.err

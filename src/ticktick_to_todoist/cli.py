"""Command line interface: argument parsing, prompting, and wiring."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import auth, csvparse, executor, layout, limits, mapping, preflight, report, sync
from .layout import MigrationPlan
from .model import STATUS_ABANDONED, STATUS_COMPLETED, Task
from .state import MigrationState, StateError

EXIT_OK = 0
EXIT_ERRORS = 1
EXIT_ABORTED = 2

DEFAULT_STATE_FILENAME = "migration-state.json"

# Each pass may raise issues created by the previous pass's resolution.
# Bounded so a resolution that keeps re-triggering itself cannot loop forever.
MAX_PREFLIGHT_PASSES = 4

EPILOG = """\
The API token is read from --token-file, then the TODOIST_API_TOKEN
environment variable, then a hidden prompt. There is deliberately no
--token option: an argument would be recorded in your shell history and
visible to other users via `ps`.

Examples:
  python3 -m ticktick_to_todoist --csv export.csv
  python3 -m ticktick_to_todoist --csv export.csv --only-list Buy --live
  python3 -m ticktick_to_todoist --csv export.csv --layout sections --live
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ticktick-to-todoist",
        description="Migrate a TickTick CSV export into Todoist.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv", help="Path to the TickTick CSV export")
    parser.add_argument("--live", action="store_true",
                        help="Actually write to Todoist. Without this the run "
                             "is a dry run and writes nothing.")
    parser.add_argument("--token-file",
                        help="File whose first non-empty line is your Todoist "
                             "API token")
    parser.add_argument("--layout", choices=["auto", "projects", "sections"],
                        default="auto",
                        help="How TickTick lists map to Todoist. 'projects' "
                             "gives each list its own project; 'sections' "
                             "makes each list a section, which fits the free "
                             "plan's 5-project limit. Default: auto.")
    parser.add_argument("--only-list", action="append", metavar="NAME",
                        help="Only migrate this TickTick list. Repeatable. "
                             "Use it for a small live test first.")
    parser.add_argument("--skip-completed", action="store_true",
                        help="Do not import tasks already completed in TickTick")
    parser.add_argument("--include-abandoned", action="store_true",
                        help="Import TickTick's abandoned tasks, tagged "
                             "'ticktick-abandoned'")
    parser.add_argument("--skip-note-lists", action="store_true",
                        help="Do not import TickTick 'Notes' lists")
    parser.add_argument("--no-metadata-footer", action="store_true",
                        help="Do not append unmappable TickTick fields to task "
                             "descriptions")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Accept the recommended resolution for every "
                             "warning instead of prompting")
    parser.add_argument("--state-file",
                        help="Where to record what was created. Defaults to "
                             "migration-state.json beside the CSV.")
    parser.add_argument("--resume", action="store_true",
                        help="Continue a previous run, skipping what it "
                             "already created")
    parser.add_argument("--undo", action="store_true",
                        help="Delete everything recorded in the state file")
    parser.add_argument("--token", help=argparse.SUPPRESS)
    return parser


def _fail(message: str) -> int:
    sys.stderr.write(message.rstrip() + "\n")
    return EXIT_ABORTED


def _filter_tasks(tasks: List[Task], options: argparse.Namespace
                  ) -> Tuple[List[Task], Optional[str]]:
    if options.only_list:
        wanted = {name.strip().lower() for name in options.only_list}
        tasks = [t for t in tasks if t.list_name.strip().lower() in wanted]
        if not tasks:
            return [], ("No rows matched --only-list {0}. Check the list names "
                        "in your export.".format(", ".join(options.only_list)))
    if options.skip_completed:
        tasks = [t for t in tasks if t.status != STATUS_COMPLETED]
    if options.skip_note_lists:
        tasks = [t for t in tasks if t.project_kind != "NOTE"]
    return tasks, None


def _ask_interactively(issue: preflight.Issue) -> str:
    sys.stdout.write(report.render_issue(issue) + "\n")
    default_index = 1
    for index, resolution in enumerate(issue.resolutions, start=1):
        if resolution.recommended:
            default_index = index
    while True:
        raw = input("Choose 1-{0} [{1}]: ".format(len(issue.resolutions),
                                                   default_index)).strip()
        if not raw:
            return issue.resolutions[default_index - 1].key
        if raw.isdigit() and 1 <= int(raw) <= len(issue.resolutions):
            return issue.resolutions[int(raw) - 1].key
        sys.stdout.write("Please enter a number between 1 and {0}.\n".format(
            len(issue.resolutions)))


_SEVERITY_ORDER = {
    preflight.SEVERITY_BLOCKER: 0,
    preflight.SEVERITY_WARNING: 1,
    preflight.SEVERITY_INFO: 2,
}


def _by_severity(issues: List[preflight.Issue]) -> List[preflight.Issue]:
    """Ask about blockers and warnings before purely informational notices.

    check() emits issues in a fixed internal order that has nothing to do
    with how urgent they are (a data-quality INFO notice can come before a
    plan-limit WARNING). Sorting here -- stably, so ties keep check()'s
    order -- means the most consequential question is always asked first.
    """
    return sorted(issues, key=lambda issue: _SEVERITY_ORDER.get(issue.severity, 3))


def _preresolved(issue_key: str, options: argparse.Namespace) -> Optional[str]:
    """Flags that answer an issue outright, so it never prompts.

    Each rule only fires when the corresponding flag was actually given;
    otherwise the issue falls through to the normal ask()/recommended path
    so it still gets a chance to be shown (interactively) or auto-accepted
    (non-interactively), exactly like any other issue.
    """
    if issue_key == "project_cap":
        if options.layout == "sections":
            return "use_sections"
        if options.layout == "projects":
            # The user explicitly forced projects; honour that instead of
            # silently switching them to sections behind their back. They
            # accept the consequence: creations past the cap will fail and
            # be reported, same as choosing "Import anyway" would do.
            return "proceed"
    if issue_key == "abandoned_rows" and options.include_abandoned:
        return "import_labelled"
    # Deliberately no tasks_per_project/--skip-completed rule: _filter_tasks()
    # already drops every completed task before the plan is built, so a
    # "skip completed" resolution at this point rebuilds an identical plan,
    # leaving the blocker in place and aborting with advice to pass the very
    # flag the user already passed. Letting the issue fall through to its
    # recommended "overflow" resolution actually fixes it.
    return None


def _counts_toward_the_project_cap(project: Dict[str, Any]) -> bool:
    """Whether an existing Todoist project consumes one of the plan's slots.

    The sync `projects` resource returns more than the user's active
    projects. Archived (and soft-deleted) projects do not count against the
    active project cap, and Inbox does not consume a slot at all. Counting
    them would push an account over the cap that isn't -- forcing a needless
    layout switch, or a hard abort on the Free plan -- and would make an
    archived project's name collide with an import target for no reason.
    """
    return not (project.get("is_archived")
                or project.get("is_deleted")
                # Sync calls it inbox_project; the REST API spells the same
                # thing is_inbox_project. Neither should ever be counted.
                or project.get("inbox_project")
                or project.get("is_inbox_project"))


def resolve_issues(issues: List[preflight.Issue], plan: MigrationPlan,
                   options: argparse.Namespace,
                   ask: Callable[[preflight.Issue], str],
                   existing_projects: Sequence[str] = (),
                   proceeded_keys: Optional[set] = None,
                   plan_limits: Optional[limits.Limits] = None
                   ) -> Tuple[MigrationPlan, bool]:
    """Apply a resolution to every issue. Returns (plan, aborted).

    Issues are asked about in severity order (blockers, then warnings, then
    purely informational notices) regardless of the order the caller passed
    them in. This is done here rather than left to the caller so the
    ordering invariant holds for anyone calling this documented interface
    directly with preflight.check()'s raw order, not just main()'s own call
    site: check() can put a low-stakes info notice (2 resolutions) ahead of
    a real decision (3 resolutions) with nothing to do with urgency, and an
    `ask` that expects the more consequential question first -- as a fixed
    interactive answer sequence naturally would -- could otherwise loop
    forever rejecting an out-of-range choice.

    `proceeded_keys`, if given, is updated in place with the key of every
    issue whose chosen resolution was literally "proceed" -- a deliberate
    "import anyway, let the real API sort it out" choice. main() uses this
    to tell that apart from a blocker that's still around only because the
    loop ran out of chances to fix it for real.

    `plan_limits` must be the same limits the issues were checked against,
    so a resolution trims to the account's real numbers rather than the
    published defaults.
    """
    for issue in _by_severity(issues):
        choice = _preresolved(issue.key, options)
        if choice is None:
            choice = ask(issue)
        if choice == "abort":
            return plan, True
        if choice == "proceed" and proceeded_keys is not None:
            proceeded_keys.add(issue.key)
        plan = preflight.apply(plan, issue.key, choice, existing_projects,
                               plan_limits)
    return plan, False


def main(argv: Optional[Sequence[str]] = None,
         stdin_isatty: Optional[bool] = None,
         transport: Optional[Callable[..., Any]] = None,
         environ: Optional[Dict[str, str]] = None) -> int:
    parser = build_parser()
    options = parser.parse_args(argv)

    # `is not None`, not truthiness: `--token ""` must trigger the same
    # rejection as any other use of the flag, not slip through unremarked.
    if options.token is not None:
        return _fail(
            "--token is not supported, because a token on the command line is "
            "saved in your shell history and visible to other users via `ps`.\n"
            "Use one of these instead:\n"
            "  --token-file /path/to/file\n"
            "  export TODOIST_API_TOKEN=...\n"
            "  omit it entirely and type it at the prompt"
        )

    if stdin_isatty is None:
        stdin_isatty = sys.stdin.isatty()
    interactive = bool(stdin_isatty) and not options.yes

    if not options.undo and not options.csv:
        return _fail("--csv is required (or use --undo with --state-file).")

    state_path = options.state_file or (
        os.path.join(os.path.dirname(os.path.abspath(options.csv)),
                     DEFAULT_STATE_FILENAME)
        if options.csv else DEFAULT_STATE_FILENAME
    )

    # ---- undo -------------------------------------------------------
    if options.undo:
        return _run_undo(options, state_path, transport, environ, interactive)

    # ---- load and map ------------------------------------------------
    try:
        tasks = mapping.load_tasks(options.csv)
    except FileNotFoundError:
        return _fail("Could not open {0}.".format(options.csv))
    except csvparse.CsvFormatError as error:
        return _fail(str(error))

    tasks, error_message = _filter_tasks(tasks, options)
    if error_message:
        return _fail(error_message)
    if not tasks:
        return _fail("There are no rows left to import after filtering.")

    # ---- token and limits --------------------------------------------
    # Only bother prompting when we're actually going to need a live
    # connection AND the caller told us stdin is interactive: a dry run can
    # fall back to assumed limits, and attempting a real prompt when stdin
    # was declared non-interactive (or --yes was given) risks an uncaught
    # EOFError from getpass instead of the clean error message below.
    try:
        token = auth.resolve_token(token_file=options.token_file,
                                   environ=environ,
                                   allow_prompt=options.live and interactive)
    except auth.TokenError as error:
        return _fail(str(error))

    if options.live and not token:
        return _fail(
            "A token is required for --live. Set TODOIST_API_TOKEN, pass "
            "--token-file, or run without --live for a dry run."
        )

    client = None
    plan_limits = limits.FREE_PLAN_ASSUMPTION
    existing_projects: List[str] = []
    if token:
        client = sync.SyncClient(token, transport=transport)
        try:
            snapshot = client.read(["user", "user_plan_limits", "projects"])
        except sync.SyncError as error:
            return _fail(auth.redact(str(error), token))
        plan_limits = limits.from_sync_response(snapshot)
        existing_projects = [p.get("name", "")
                             for p in (snapshot.get("projects") or [])
                             if _counts_toward_the_project_cap(p)]
        email = (snapshot.get("user") or {}).get("email")
        if options.live and email:
            sys.stdout.write(
                "Migrating into the Todoist account for {0}\n".format(email))

    # ---- build the plan ----------------------------------------------
    mode = layout.LAYOUT_SECTIONS if options.layout == "sections" \
        else layout.LAYOUT_PROJECTS
    plan = layout.build_plan(tasks, mode)

    existing_count = len(existing_projects)
    if options.layout == "auto":
        if len(plan.projects) + existing_count > plan_limits.max_projects:
            candidate = layout.build_plan(tasks, layout.LAYOUT_SECTIONS)
            if len(candidate.projects) + existing_count <= plan_limits.max_projects:
                sys.stdout.write(
                    "Auto layout: {0} projects would exceed your limit of {1}, "
                    "so lists become sections instead.\n".format(
                        len(plan.projects) + existing_count,
                        plan_limits.max_projects))
                plan = candidate

    # ---- state -------------------------------------------------------
    try:
        state = MigrationState.load(state_path)
    except StateError as error:
        return _fail(str(error))

    if options.live and not options.resume and MigrationState.exists(state_path):
        sys.stdout.write(
            "\nA state file already exists at {0}, so a previous run created "
            "things in Todoist.\nRe-run with --resume to continue it, or "
            "delete that file to start fresh.\n".format(state_path))
        return EXIT_ABORTED
    if not options.resume:
        state = MigrationState(state_path)

    # ---- preflight ----------------------------------------------------
    if interactive:
        ask = _ask_interactively
    else:
        def ask(issue):
            choice = issue.recommended
            sys.stdout.write(report.render_issue(issue) + "\n")
            sys.stdout.write("-> taking the recommended option: {0}\n".format(
                choice.label))
            return choice.key

    # Resolutions can create new problems -- spilling tasks into a sibling
    # project costs a project, which may then breach the project cap. Re-check
    # after each pass and raise anything new rather than hiding it.
    seen_issue_keys = set()
    proceeded_keys: set = set()
    for _pass in range(MAX_PREFLIGHT_PASSES):
        # Order doesn't matter here -- resolve_issues() below sorts by
        # severity itself -- this is just picking out what's new.
        issues = preflight.check(plan, plan_limits, existing_projects,
                                 token_present=bool(token))
        fresh = [i for i in issues if i.key not in seen_issue_keys]
        if not fresh:
            break
        seen_issue_keys.update(i.key for i in fresh)
        plan, aborted = resolve_issues(fresh, plan, options, ask,
                                       existing_projects, proceeded_keys,
                                       plan_limits)
        if aborted:
            sys.stdout.write("\nStopped. Nothing was written.\n")
            return EXIT_ABORTED

    # The safety net below must not depend on *how* the loop above ended --
    # `break` (converged, nothing left that we haven't already seen) and
    # exhausting every pass both leave open the possibility that a blocker
    # is still sitting on the plan, so re-check unconditionally rather than
    # only in a `for...else` that only fires when the loop was NOT broken.
    #
    # A blocker surviving to here falls into one of two very different
    # buckets, and only one of them is safe to let through:
    #
    #  - It was explicitly resolved via "proceed" ("Import anyway" -- a
    #    real, documented resolution for project_cap/tasks_per_project/
    #    sections_per_project that preflight.apply() deliberately leaves as
    #    a no-op). For a --live run that is fine -- the real Todoist API
    #    enforces the limit itself and the failures land in result.errors,
    #    already reported below. Tracked via `proceeded_keys`.
    #
    #  - It was never actually resolved: the loop ran out of passes, or --
    #    just as easily -- converged via `break` only because a LATER
    #    resolution (e.g. project_cap's "use_sections" rebuilding the whole
    #    plan from the raw tasks) silently undid an EARLIER fix for a
    #    still-blocking issue whose key had already been marked seen. That
    #    case must hard-stop before any writes happen, for live runs just
    #    as much as dry runs -- the loop's convergence was accidental, not
    #    a decision anyone made. A dry run additionally has no backstop of
    #    its own at all: executor.execute(dry_run=True) fakes every id and
    #    never talks to the API, so without this check it would print a
    #    clean "DRY RUN -- nothing was written" summary while hiding that
    #    the same plan run live would fail.
    remaining = preflight.check(plan, plan_limits, existing_projects,
                                token_present=bool(token))
    blockers = [i for i in remaining if i.severity == preflight.SEVERITY_BLOCKER]
    unresolved = [i for i in blockers
                  if not (options.live and i.key in proceeded_keys)]
    if unresolved:
        return _fail(
            "Could not find a combination that fits your plan's limits:\n"
            + "\n".join("  - " + i.message for i in unresolved)
            + "\n\nConsider --only-list to import a smaller slice, "
              "--layout sections to fit more lists under your project cap, "
              "or upgrading your Todoist plan."
        )

    sys.stdout.write("\n" + report.render_plan_summary(plan, plan_limits) + "\n")

    # ---- execute ------------------------------------------------------
    if not options.live:
        client = client or sync.SyncClient("", transport=transport)
    try:
        result = executor.execute(
            plan, client, state,
            dry_run=not options.live,
            metadata_footer=not options.no_metadata_footer,
            on_progress=lambda message: sys.stdout.write(
                "  ... {0}\n".format(message)),
        )
    except sync.SyncError as error:
        # A whole-request failure partway through a live run: the network
        # dropped, the server kept failing after retries, or the token was
        # revoked mid-run. Everything created so far is already on disk
        # (executor saves state after each request), so this is recoverable
        # -- say so instead of dying with a raw traceback.
        sys.stderr.write(
            "The migration stopped partway through: {0}\n".format(
                auth.redact(str(error), token))
            + "Whatever was created so far is recorded in {0}.\n".format(
                state_path)
            + "Re-run the same command with --resume to continue where it "
              "left off.\n"
        )
        return EXIT_ERRORS

    # result.errors carries whatever message Todoist (or a misbehaving
    # transport) sent back for a failed command; redact defensively, the
    # same way the earlier client.read() failure path already does.
    sys.stdout.write(
        auth.redact(report.render_result(plan, result, not options.live), token)
        + "\n")
    if not options.live:
        sys.stdout.write(
            "\nThis was a dry run. Re-run with --live to write to Todoist.\n")
    return EXIT_ERRORS if result.errors else EXIT_OK


def _run_undo(options, state_path, transport, environ, interactive) -> int:
    if not MigrationState.exists(state_path):
        return _fail("No state file at {0}, so there is nothing to undo.".format(
            state_path))
    try:
        state = MigrationState.load(state_path)
    except StateError as error:
        return _fail(str(error))

    commands = state.undo_commands()
    if not commands:
        state.clear()
        sys.stdout.write("Nothing was recorded as created. State file removed.\n")
        return EXIT_OK

    try:
        token = auth.resolve_token(token_file=options.token_file,
                                   environ=environ, allow_prompt=interactive)
    except auth.TokenError as error:
        return _fail(str(error))
    if not token:
        return _fail("A token is required to undo. Set TODOIST_API_TOKEN or "
                     "pass --token-file.")

    if interactive:
        answer = input(
            "Delete {0} project(s) and {1} task(s) created by the previous run? "
            "[y/N]: ".format(len(state.projects), len(state.tasks))).strip().lower()
        if answer not in ("y", "yes"):
            sys.stdout.write("Left everything alone.\n")
            return EXIT_ABORTED

    client = sync.SyncClient(token, transport=transport)
    errors = []
    try:
        for start in range(0, len(commands), sync.MAX_COMMANDS_PER_REQUEST):
            chunk = commands[start:start + sync.MAX_COMMANDS_PER_REQUEST]
            errors.extend(client.execute(chunk).errors)
    except sync.SyncError as error:
        # Same redact-before-surfacing treatment as the read-side failure
        # in main(): a whole-request failure here must not be allowed to
        # crash with a token-bearing traceback instead of a clean message.
        return _fail(auth.redact(str(error), token))

    if errors:
        sys.stdout.write("{0} deletion(s) failed:\n".format(len(errors)))
        for command_type, description, message in errors:
            sys.stdout.write("  - {0} ({1}): {2}\n".format(
                command_type, description, auth.redact(message, token)))
        return EXIT_ERRORS

    state.clear()
    sys.stdout.write("Undone. State file removed.\n")
    return EXIT_OK

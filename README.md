# ticktick-to-todoist-migrator

Migrate a TickTick CSV export into Todoist — with a full preview before
anything gets written.

## See it in action

```bash
$ python3 -m ticktick_to_todoist --csv ticktick_export.csv
```

```
================================================================
MIGRATION PLAN
================================================================
Layout: projects
Plan limits: free (assumed) (assumed -- no token supplied)
Projects: 3 of 5 allowed

  To-Read  (2 active task(s))
  Buy  (0 active task(s))
  Chores  (6 active task(s))

Tasks: 17 total, 8 active, 9 completed

================================================================
MIGRATION SUMMARY  (DRY RUN -- nothing was written)
================================================================
Projects created:  3
Sections created:  0
Tasks created:     17
Tasks completed:   9

No errors reported.

This was a dry run. Re-run with --live to write to Todoist.
```

Nothing is written to Todoist until you add `--live`. Run it as many times
as you want first — it's read-only by default. (Output trimmed above: with
no token supplied, you're first asked to confirm proceeding with assumed
Free-plan limits — add `--yes` to skip that and every other prompt
automatically.)

## What it does

- Reads a TickTick CSV backup, maps folders/lists/tasks/subtasks onto
  Todoist projects/sections/tasks/subtasks.
- Defaults to a dry run — shows exactly what it would create, writes
  nothing until `--live`.
- Flags anything that won't fit cleanly (plan limits, unconvertible repeat
  rules, and more) before it touches your account, and lets you choose how
  to handle each one.
- Records everything it creates, so a `--live` run can be resumed after an
  interruption or undone entirely.

## Install

Requires Python 3.9+. No third-party dependencies.

```bash
git clone https://github.com/cbenning/ticktick-to-todoist-migrator.git
cd ticktick-to-todoist-migrator
pip install -e .
```

## Setup

1. **Export your TickTick data** — TickTick app → Settings → Backup →
   "Backup now" → downloads a `.csv`.
2. **Get a Todoist API token** — Todoist → Settings → Integrations →
   Developer → copy the token shown there. Free on every plan.
3. **Supply the token**, one of three ways: `--token-file PATH`, the
   `TODOIST_API_TOKEN` environment variable, or (if you skip both) a hidden
   prompt when a run actually needs one. There's deliberately no `--token`
   flag — a token on the command line ends up in your shell history.

## Run it

```bash
# 1. Dry run first — writes nothing
python3 -m ticktick_to_todoist --csv ticktick_export.csv

# 2. Smoke-test one list for real
export TODOIST_API_TOKEN=your_token_here
python3 -m ticktick_to_todoist --csv ticktick_export.csv --only-list Buy --live

# 3. Run everything
python3 -m ticktick_to_todoist --csv ticktick_export.csv --live
```

`--only-list` (repeatable) restricts a run to specific TickTick list names —
the recommended way to test against your real account first.

## What maps to what

```
  TickTick                          Todoist
  ─────────                         ────────
  Folder ──────────────────────────► Project
    └── List ───────────────────────►   └── Sub-project   (--layout projects)
                                        or Section        (--layout sections)
          └── Task ─────────────────►         └── Task
                └── Subtask ────────►               └── Sub-task

  Title ───────────────────────────► Task name
  Content ─────────────────────────► Description
  Tags ────────────────────────────► Labels
  Priority (0/1/3/5) ──────────────► Priority (1/2/3/4, inverted)
  Due Date + Is All Day ───────────► Due date
  Repeat (simple RRULE) ───────────► Recurring due date
  Repeat (complex RRULE) ──────  ✗   flagged for manual setup
  Status: Completed ───────────────► Completed, original date kept
  Status: Abandoned ────────────  ✗   skipped by default
  Reminder, Created Time, Order ✗   no Todoist equivalent, discarded
```

The one thing that's ever preserved from a discarded field: when a `Repeat`
rule is too complex to convert, its original text is appended to the task's
description by default (`--no-metadata-footer` to turn that off). Everything
else marked ✗ is simply gone — keep your original export if you need it.

## Todoist plan limits

Every plan: 300 active tasks/project, 20 sections/project, 500 labels/
account, 100 labels/task, 500-char titles, 16,383-char descriptions.
Completed tasks don't count toward the 300-task cap.

**Free plan only:** 5 projects total — and that includes projects you
already have. The tool checks your real account via the API and, if you're
over, offers to fold TickTick lists into sections of fewer projects instead
(`--layout sections`).

## When something needs a decision

Before writing anything, the tool checks the plan against your account and
flags problems — too many projects, a project over its task/section cap, a
repeat rule it can't convert, and so on. Interactively you're prompted for
each one with a sensible default; with `--yes` (or non-interactively) the
default is taken automatically. Run `--help` to see which flags pre-answer
specific checks (`--layout`, `--include-abandoned`, `--skip-completed`).

## Flags

Run `--help` for the full list. The ones worth knowing up front:

| Flag | What it does |
|---|---|
| `--live` | Actually write to Todoist (default is a dry run) |
| `--only-list NAME` | Restrict to one TickTick list — repeatable |
| `--layout {auto,projects,sections}` | Force a layout instead of auto-detecting what fits your plan |
| `--yes` / `-y` | Accept every recommended resolution instead of prompting |
| `--resume` | Continue an interrupted `--live` run |
| `--undo` | Delete everything a `--live` run created |

## Recovering from a bad run

**Interrupted?** (network drop, Ctrl-C, crash) Re-run the same command with
`--resume` added — anything already created is skipped.

**Want to undo?** This deletes everything that run created:

```bash
python3 -m ticktick_to_todoist --undo --csv ticktick_export.csv
```

Needs a token (same three ways as above), and asks for confirmation first
when run interactively.

## Known gaps

- Checklist handling is untested against a real TickTick export (works
  against synthetic test data; flagged at preflight so you can spot-check).
- Attachments, comments, and habits aren't in the CSV export, so they don't
  migrate.
- Rows with no `taskId` get re-created on `--resume` (real exports always
  have one).
- A TickTick folder literally named "Imported" merges with the
  sections-layout fallback project of that name.

## Licence

MIT. See [LICENSE](LICENSE).

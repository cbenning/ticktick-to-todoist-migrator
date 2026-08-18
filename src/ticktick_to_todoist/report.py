"""Renders human-readable summaries of a plan and its execution."""

from __future__ import annotations

from typing import List

from .executor import ExecutionResult
from .layout import LAYOUT_SECTIONS, MigrationPlan, project_task_counts
from .limits import Limits
from .model import STATUS_COMPLETED
from .preflight import Issue

RULE = "=" * 64


def render_plan_summary(plan: MigrationPlan, plan_limits: Limits) -> str:
    counts = project_task_counts(plan)
    sections_by_project = {}
    for section in plan.sections:
        sections_by_project.setdefault(section.project_key, []).append(section.name)

    lines = [RULE, "MIGRATION PLAN", RULE]
    lines.append("Layout: {0}".format(plan.layout))
    lines.append("Plan limits: {0}{1}".format(
        plan_limits.plan_name,
        " (assumed -- no token supplied)" if plan_limits.assumed else "",
    ))
    lines.append("Projects: {0} of {1} allowed".format(
        len(plan.projects), plan_limits.max_projects))
    lines.append("")

    for project in plan.projects:
        active = counts.get(project.key, 0)
        lines.append("  {0}  ({1} active task(s))".format(project.name, active))
        for section_name in sections_by_project.get(project.key, []):
            lines.append("      # {0}".format(section_name))

    total = len(plan.tasks)
    completed = sum(1 for p in plan.tasks if p.task.status == STATUS_COMPLETED)
    lines.append("")
    lines.append("Tasks: {0} total, {1} active, {2} completed".format(
        total, total - completed, completed))
    if plan.layout == LAYOUT_SECTIONS:
        lines.append("Sections: {0}".format(len(plan.sections)))
    return "\n".join(lines)


def render_issue(issue: Issue) -> str:
    lines = ["", "[{0}] {1}".format(issue.severity.upper(), issue.message), ""]
    for index, resolution in enumerate(issue.resolutions, start=1):
        marker = " (recommended)" if resolution.recommended else ""
        lines.append("  {0}. {1}{2}".format(index, resolution.label, marker))
        lines.append("     {0}".format(resolution.description))
    return "\n".join(lines)


def render_result(plan: MigrationPlan, result: ExecutionResult,
                  dry_run: bool) -> str:
    heading = "MIGRATION SUMMARY"
    if dry_run:
        heading += "  (DRY RUN -- nothing was written)"
    lines = ["", RULE, heading, RULE]
    lines.append("Projects created:  {0}".format(result.projects_created))
    lines.append("Sections created:  {0}".format(result.sections_created))
    lines.append("Tasks created:     {0}".format(result.tasks_created))
    lines.append("Tasks completed:   {0}".format(result.tasks_completed))
    if result.skipped_existing:
        lines.append("Skipped (already imported): {0}".format(
            result.skipped_existing))

    manual: List[str] = [
        p.task.title for p in plan.tasks
        if p.task.repeat_raw and not p.task.repeat_converted
    ]
    if manual:
        lines.append("")
        lines.append("{0} task(s) need their recurrence set by hand in "
                     "Todoist:".format(len(manual)))
        for title in manual:
            lines.append("  - {0}".format(title))

    if result.errors:
        lines.append("")
        lines.append("{0} command(s) failed:".format(len(result.errors)))
        for command_type, description, message in result.errors:
            lines.append("  - {0} ({1}): {2}".format(command_type, description,
                                                     message))
    else:
        lines.append("")
        lines.append("No errors reported.")
    return "\n".join(lines)

"""Detects problems in a migration plan and offers resolutions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from . import layout as layout_mod
from .limits import SECTIONS_PER_PROJECT, TASKS_PER_PROJECT, Limits
from .model import STATUS_ABANDONED, STATUS_COMPLETED, Task

SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass(frozen=True)
class Resolution:
    key: str
    label: str
    description: str
    recommended: bool = False


@dataclass(frozen=True)
class Issue:
    key: str
    severity: str
    message: str
    resolutions: Tuple[Resolution, ...]

    @property
    def recommended(self) -> Resolution:
        for resolution in self.resolutions:
            if resolution.recommended:
                return resolution
        return self.resolutions[0]


def check(plan: layout_mod.MigrationPlan, plan_limits: Limits,
          existing_projects: Sequence[str] = (),
          token_present: bool = True) -> List[Issue]:
    issues = []
    issues.extend(_check_project_cap(plan, plan_limits,
                                     len(existing_projects)))
    issues.extend(_check_tasks_per_project(plan, plan_limits))
    issues.extend(_check_sections_per_project(plan, plan_limits))
    issues.extend(_check_labels(plan, plan_limits))
    issues.extend(_check_lengths(plan, plan_limits))
    issues.extend(_check_duplicate_names(plan, existing_projects))
    issues.extend(_check_data_quality(plan))
    if not token_present and plan_limits.assumed:
        issues.append(Issue(
            key="assumed_limits",
            severity=SEVERITY_WARNING,
            message=("No API token was supplied, so plan limits are assumed to "
                     "be the Free plan's (5 projects). Supply a token to check "
                     "against your real limits."),
            resolutions=(
                Resolution("proceed", "Proceed with assumed limits",
                           "Continue, treating the numbers above as estimates.",
                           recommended=True),
                Resolution("abort", "Stop",
                           "Exit so you can supply a token."),
            ),
        ))
    return issues


def _check_project_cap(plan, plan_limits, existing_project_count):
    wanted = len(plan.projects)
    total = wanted + existing_project_count
    if total <= plan_limits.max_projects:
        return []
    message = (
        "This import needs {0} project(s) and your account already has {1}, "
        "which is {2} over your plan's limit of {3}."
    ).format(wanted, existing_project_count,
             total - plan_limits.max_projects, plan_limits.max_projects)
    return [Issue(
        key="project_cap",
        severity=SEVERITY_BLOCKER,
        message=message,
        resolutions=(
            Resolution("use_sections", "Use sections instead of projects",
                       "Each TickTick folder becomes a project and each list "
                       "becomes a section inside it. Lists with no folder share "
                       "one 'Imported' project. Nothing is lost.",
                       recommended=True),
            Resolution("proceed", "Import anyway",
                       "Projects past your limit will fail; the failures are "
                       "listed at the end."),
            Resolution("abort", "Stop",
                       "Exit without writing anything."),
        ),
    )]


def _check_tasks_per_project(plan, plan_limits):
    counts = layout_mod.project_task_counts(plan)
    names = {p.key: p.name for p in plan.projects}
    over = [(names.get(k, k), v) for k, v in counts.items()
            if v > plan_limits.tasks_per_project]
    if not over:
        return []
    detail = ", ".join("{0} ({1} active tasks)".format(n, v) for n, v in over)
    return [Issue(
        key="tasks_per_project",
        severity=SEVERITY_BLOCKER,
        message=("Todoist allows {0} active tasks per project on every plan. "
                 "Over the limit: {1}.").format(plan_limits.tasks_per_project,
                                                detail),
        resolutions=(
            Resolution("overflow", "Spill into numbered sibling projects",
                       "Extra tasks go to 'Name (2)', 'Name (3)' and so on. "
                       "This costs extra projects, which may then exceed your "
                       "project limit -- preflight re-checks afterwards.",
                       recommended=True),
            Resolution("skip_completed", "Skip completed tasks",
                       "Completed tasks do not count toward this limit, so "
                       "this only helps if you also drop active ones."),
            Resolution("proceed", "Import anyway",
                       "Tasks past the limit will fail and be reported."),
            Resolution("abort", "Stop", "Exit without writing anything."),
        ),
    )]


def _check_sections_per_project(plan, plan_limits):
    counts: Dict[str, int] = {}
    for section in plan.sections:
        counts[section.project_key] = counts.get(section.project_key, 0) + 1
    names = {p.key: p.name for p in plan.projects}
    over = [(names.get(k, k), v) for k, v in counts.items()
            if v > plan_limits.sections_per_project]
    if not over:
        return []
    detail = ", ".join("{0} ({1} sections)".format(n, v) for n, v in over)
    return [Issue(
        key="sections_per_project",
        severity=SEVERITY_BLOCKER,
        message=("Todoist allows {0} sections per project. Over the limit: "
                 "{1}.").format(plan_limits.sections_per_project, detail),
        resolutions=(
            Resolution("overflow", "Spill into numbered sibling projects",
                       "Sections past the limit move to 'Name (2)'.",
                       recommended=True),
            Resolution("proceed", "Import anyway",
                       "Sections past the limit will fail and be reported."),
            Resolution("abort", "Stop", "Exit without writing anything."),
        ),
    )]


def _check_labels(plan, plan_limits):
    issues = []
    distinct = set()
    heavy = []
    for planned in plan.tasks:
        distinct.update(planned.task.labels)
        if len(planned.task.labels) > plan_limits.labels_per_task:
            heavy.append(planned.task.title)
    if heavy:
        issues.append(Issue(
            key="labels_per_task",
            severity=SEVERITY_WARNING,
            message=("{0} task(s) carry more than the {1}-label-per-task limit, "
                     "starting with '{2}'.").format(
                         len(heavy), plan_limits.labels_per_task, heavy[0]),
            resolutions=(
                Resolution("drop_excess", "Keep the first {0} labels".format(
                    plan_limits.labels_per_task),
                    "Excess labels are dropped and listed in the report.",
                    recommended=True),
                Resolution("proceed", "Import anyway",
                           "Those tasks will fail and be reported."),
                Resolution("abort", "Stop", "Exit without writing anything."),
            ),
        ))
    if len(distinct) > plan_limits.labels_per_account:
        issues.append(Issue(
            key="labels_per_account",
            severity=SEVERITY_WARNING,
            message=("This import would create {0} distinct labels; Todoist "
                     "allows {1} per account.").format(
                         len(distinct), plan_limits.labels_per_account),
            resolutions=(
                Resolution("drop_labels", "Import without labels",
                           "Tags are appended to each task's description "
                           "instead of becoming labels.",
                           recommended=True),
                Resolution("proceed", "Import anyway",
                           "Labels past the limit will fail and be reported."),
                Resolution("abort", "Stop", "Exit without writing anything."),
            ),
        ))
    return issues


def _check_lengths(plan, plan_limits):
    issues = []
    long_titles = [p.task for p in plan.tasks
                   if len(p.task.title) > plan_limits.max_title]
    long_bodies = [p.task for p in plan.tasks
                   if len(p.task.description) > plan_limits.max_description]
    if long_titles:
        issues.append(Issue(
            key="long_title",
            severity=SEVERITY_WARNING,
            message=("{0} task title(s) exceed Todoist's {1}-character "
                     "limit.").format(len(long_titles), plan_limits.max_title),
            resolutions=(
                Resolution("truncate", "Truncate the title",
                           "The title is cut to the limit and the full "
                           "original is prepended to the description.",
                           recommended=True),
                Resolution("proceed", "Import anyway",
                           "Those tasks will fail and be reported."),
                Resolution("abort", "Stop", "Exit without writing anything."),
            ),
        ))
    if long_bodies:
        issues.append(Issue(
            key="long_description",
            severity=SEVERITY_WARNING,
            message=("{0} task description(s) exceed Todoist's {1}-character "
                     "limit.").format(len(long_bodies),
                                      plan_limits.max_description),
            resolutions=(
                Resolution("truncate", "Truncate the description",
                           "The description is cut to the limit with a marker "
                           "noting it was shortened.",
                           recommended=True),
                Resolution("proceed", "Import anyway",
                           "Those tasks will fail and be reported."),
                Resolution("abort", "Stop", "Exit without writing anything."),
            ),
        ))
    return issues


def _check_duplicate_names(plan, existing_projects):
    if not existing_projects:
        return []
    existing = {name.strip().lower() for name in existing_projects if name}
    clashing = [p.name for p in plan.projects
                if p.name.strip().lower() in existing]
    if not clashing:
        return []
    return [Issue(
        key="duplicate_project",
        severity=SEVERITY_WARNING,
        message=("Your Todoist account already has a project named: {0}. "
                 "Importing would leave you with two projects sharing a "
                 "name.").format(", ".join(sorted(set(clashing)))),
        resolutions=(
            Resolution("rename", "Rename the imported project",
                       "The new project is created as 'Name (imported)', "
                       "leaving your existing one untouched.",
                       recommended=True),
            Resolution("proceed", "Create it anyway",
                       "You end up with two projects of the same name. "
                       "Todoist allows this."),
            Resolution("abort", "Stop", "Exit without writing anything."),
        ),
    )]


def _check_data_quality(plan):
    issues = []
    tasks = [p.task for p in plan.tasks]

    unconverted = [t for t in tasks if t.repeat_raw and not t.repeat_converted]
    if unconverted:
        issues.append(Issue(
            key="unconverted_repeat",
            severity=SEVERITY_INFO,
            message=("{0} task(s) use a repeat rule too complex to convert "
                     "safely. They will be imported with their next due date "
                     "only, and listed at the end so you can set the "
                     "recurrence by hand.").format(len(unconverted)),
            resolutions=(
                Resolution("proceed", "Import as one-off due dates",
                           "The original rule is kept in the description.",
                           recommended=True),
                Resolution("abort", "Stop", "Exit without writing anything."),
            ),
        ))

    checklists = [t for t in tasks if t.is_checklist]
    if checklists:
        issues.append(Issue(
            key="checklist_rows",
            severity=SEVERITY_INFO,
            message=("{0} task(s) are TickTick checklists. If their items are "
                     "separate CSV rows they become Todoist sub-tasks; if they "
                     "are inline text they arrive in the description. Worth "
                     "spot-checking these after the import.").format(
                         len(checklists)),
            resolutions=(
                Resolution("proceed", "Import them",
                           "Continue and check the result afterwards.",
                           recommended=True),
                Resolution("abort", "Stop", "Exit without writing anything."),
            ),
        ))

    abandoned = [t for t in tasks if t.status == STATUS_ABANDONED]
    if abandoned:
        issues.append(Issue(
            key="abandoned_rows",
            severity=SEVERITY_WARNING,
            message=("{0} task(s) are marked Abandoned in TickTick. Todoist "
                     "has no equivalent state, so importing them would make "
                     "them look like live work.").format(len(abandoned)),
            resolutions=(
                Resolution("skip", "Leave them behind",
                           "Abandoned tasks are not imported.",
                           recommended=True),
                Resolution("import_labelled", "Import with a label",
                           "They arrive as active tasks tagged "
                           "'ticktick-abandoned'."),
                Resolution("abort", "Stop", "Exit without writing anything."),
            ),
        ))

    broken = _broken_parents(tasks)
    if broken:
        issues.append(Issue(
            key="broken_parent",
            severity=SEVERITY_WARNING,
            message=("{0} task(s) reference a parent that is missing from the "
                     "export or form a loop.").format(len(broken)),
            resolutions=(
                Resolution("flatten", "Import them as top-level tasks",
                           "The broken parent link is dropped; nothing is lost.",
                           recommended=True),
                Resolution("skip", "Leave them behind",
                           "Those tasks are not imported."),
                Resolution("abort", "Stop", "Exit without writing anything."),
            ),
        ))
    return issues


def _broken_parents(tasks: Sequence[Task]) -> List[Task]:
    by_id = {t.row_id: t for t in tasks if t.row_id}
    broken = []
    for task in tasks:
        if not task.parent_row_id:
            continue
        if task.parent_row_id not in by_id:
            broken.append(task)
            continue
        seen = {task.row_id}
        cursor = by_id[task.parent_row_id]
        while cursor.parent_row_id and cursor.parent_row_id in by_id:
            if cursor.row_id in seen:
                broken.append(task)
                break
            seen.add(cursor.row_id)
            cursor = by_id[cursor.parent_row_id]
    return broken


# ----------------------------------------------------------------------
# Resolution application
# ----------------------------------------------------------------------

ABANDONED_LABEL = "ticktick-abandoned"
TRUNCATION_MARKER = "\n\n[truncated by ticktick-to-todoist]"


def apply(plan: layout_mod.MigrationPlan, issue_key: str, resolution_key: str,
          existing_projects: Sequence[str] = (),
          plan_limits: Optional[Limits] = None) -> layout_mod.MigrationPlan:
    """Return a new plan with the chosen resolution applied.

    The tasks are read back out of the plan rather than passed in separately,
    so applying two resolutions in sequence compounds correctly.

    `plan_limits` must be the same Limits the issue was raised against by
    check(): a resolution that trimmed to a hardcoded constant instead would
    silently become a no-op the moment an account's real limits differ from
    the defaults, and the run would then die claiming no combination fits
    even though the recommended resolution would have worked. It defaults to
    the published per-plan numbers only so callers with nothing better can
    still apply a resolution.

    'proceed' is always a no-op. 'abort' is handled by the caller, not here.
    """
    if plan_limits is None:
        plan_limits = Limits()
    if resolution_key in ("proceed", "abort"):
        return plan

    tasks = [p.task for p in plan.tasks]
    mode = plan.layout

    if issue_key == "project_cap" and resolution_key == "use_sections":
        return layout_mod.build_plan(tasks, layout_mod.LAYOUT_SECTIONS)

    if issue_key == "duplicate_project" and resolution_key == "rename":
        existing = {name.strip().lower() for name in existing_projects if name}
        renamed = tuple(
            replace(p, name="{0} (imported)".format(p.name))
            if p.name.strip().lower() in existing else p
            for p in plan.projects
        )
        return layout_mod.MigrationPlan(plan.layout, renamed, plan.sections,
                                        plan.tasks)

    if issue_key in ("tasks_per_project", "sections_per_project"):
        if resolution_key == "overflow":
            if issue_key == "sections_per_project":
                return _apply_section_overflow(
                    plan, plan_limits.sections_per_project)
            return _apply_overflow(plan, plan_limits.tasks_per_project)
        if resolution_key == "skip_completed":
            kept = [t for t in tasks if t.status != STATUS_COMPLETED]
            return layout_mod.build_plan(kept, mode)

    if issue_key == "abandoned_rows":
        if resolution_key == "skip":
            kept = [t for t in tasks if t.status != STATUS_ABANDONED]
            return layout_mod.build_plan(kept, mode)
        if resolution_key == "import_labelled":
            relabelled = [
                replace(t, labels=tuple(t.labels) + (ABANDONED_LABEL,))
                if t.status == STATUS_ABANDONED else t
                for t in tasks
            ]
            return layout_mod.build_plan(relabelled, mode)

    if issue_key == "broken_parent":
        broken_ids = {t.row_id for t in _broken_parents(tasks)}
        if resolution_key == "flatten":
            fixed = [replace(t, parent_row_id="") if t.row_id in broken_ids else t
                     for t in tasks]
            return layout_mod.build_plan(fixed, mode)
        if resolution_key == "skip":
            kept = [t for t in tasks if t.row_id not in broken_ids]
            return layout_mod.build_plan(kept, mode)

    if issue_key == "long_title" and resolution_key == "truncate":
        limit = plan_limits.max_title
        fixed = []
        for t in tasks:
            if len(t.title) <= limit:
                fixed.append(t)
                continue
            body = "Full title: {0}\n\n{1}".format(t.title, t.description).strip()
            fixed.append(replace(t, title=t.title[:limit], description=body))
        return layout_mod.build_plan(fixed, mode)

    if issue_key == "long_description" and resolution_key == "truncate":
        limit = plan_limits.max_description
        keep = limit - len(TRUNCATION_MARKER)
        fixed = [replace(t, description=t.description[:keep] + TRUNCATION_MARKER)
                 if len(t.description) > limit else t
                 for t in tasks]
        return layout_mod.build_plan(fixed, mode)

    if issue_key == "labels_per_task" and resolution_key == "drop_excess":
        limit = plan_limits.labels_per_task
        fixed = [replace(t, labels=t.labels[:limit]) if len(t.labels) > limit
                 else t
                 for t in tasks]
        return layout_mod.build_plan(fixed, mode)

    if issue_key == "labels_per_account" and resolution_key == "drop_labels":
        fixed = []
        for t in tasks:
            if not t.labels:
                fixed.append(t)
                continue
            body = "{0}\n\nTickTick tags: {1}".format(
                t.description, ", ".join(t.labels)).strip()
            fixed.append(replace(t, labels=(), description=body))
        return layout_mod.build_plan(fixed, mode)

    return plan


def _apply_overflow(plan: layout_mod.MigrationPlan,
                    tasks_per_project: int = TASKS_PER_PROJECT
                    ) -> layout_mod.MigrationPlan:
    """Move tasks past the per-project cap into numbered sibling projects.

    Sections are kept atomic: a section can only belong to one project, so
    all of a section's tasks always move together as a single unit -- this
    includes that section's completed tasks, even though completed tasks
    don't count toward the cap and aren't part of the bucketing math. A task
    with no section (LAYOUT_PROJECTS mode, or a loose task in sections mode)
    is its own movable unit, and completed unsectioned tasks never move
    (unaffected by bucketing, same as before). Units are walked in the order
    they first appear within their project and packed into buckets of at
    most `tasks_per_project` active tasks each; bucket 0 stays on the
    original project, bucket N > 0 moves to '{project_key}#{N + 1}'.
    """
    from .layout import PlannedProject

    projects = list(plan.projects)
    names = {p.key: p.name for p in plan.projects}
    # A sibling project must stay under the same folder parent as the
    # project it spilled out of, or spilling silently un-nests it.
    parents = {p.key: p.parent_key for p in plan.projects}

    # Partition each project's non-completed tasks into ordered units: one
    # unit per distinct section_key (grouping all its tasks at the position
    # of its first occurrence), or one unit per individual task when there
    # is no section. Only active tasks feed the bucketing math; completed
    # tasks belonging to a section that moves are reassigned afterwards in a
    # separate pass over ALL of plan.tasks, since a section drags every one
    # of its tasks with it regardless of status.
    project_units: Dict[str, List[Tuple[Optional[str], List]]] = {}
    unit_index: Dict[Tuple[str, str], int] = {}
    for planned in plan.tasks:
        if planned.task.status == STATUS_COMPLETED:
            continue
        units = project_units.setdefault(planned.project_key, [])
        if planned.section_key is None:
            units.append((None, [planned]))
        else:
            key = (planned.project_key, planned.section_key)
            if key in unit_index:
                units[unit_index[key]][1].append(planned)
            else:
                unit_index[key] = len(units)
                units.append((planned.section_key, [planned]))

    section_new_project: Dict[str, str] = {}
    task_new_project: Dict[str, str] = {}
    extra_projects: List = []

    for project_key, units in project_units.items():
        bucket = 0
        bucket_count = 0
        for section_key, unit_tasks in units:
            unit_size = len(unit_tasks)
            if bucket_count > 0 and bucket_count + unit_size > tasks_per_project:
                bucket += 1
                bucket_count = 0
            bucket_count += unit_size
            if bucket == 0:
                continue
            overflow_key = "{0}#{1}".format(project_key, bucket + 1)
            if overflow_key not in {p.key for p in extra_projects}:
                extra_projects.append(PlannedProject(
                    key=overflow_key,
                    name="{0} ({1})".format(names.get(project_key, project_key),
                                            bucket + 1),
                    parent_key=parents.get(project_key),
                ))
            if section_key is not None:
                section_new_project[section_key] = overflow_key
            else:
                for t in unit_tasks:
                    task_new_project[t.task.row_id] = overflow_key

    # Reassign every task, not just the ones seen while bucketing: a task
    # whose section moved must follow it even if the task itself is
    # completed (and so was excluded from project_units above).
    reassigned = []
    for planned in plan.tasks:
        if planned.section_key is not None and planned.section_key in section_new_project:
            reassigned.append(replace(
                planned, project_key=section_new_project[planned.section_key]))
        elif planned.task.row_id in task_new_project:
            reassigned.append(replace(
                planned, project_key=task_new_project[planned.task.row_id]))
        else:
            reassigned.append(planned)
    reassigned = tuple(reassigned)

    rebuilt_sections = tuple(
        replace(section, project_key=section_new_project[section.key])
        if section.key in section_new_project else section
        for section in plan.sections
    )

    return layout_mod.MigrationPlan(
        plan.layout, tuple(projects + extra_projects), rebuilt_sections,
        reassigned,
    )


def _apply_section_overflow(
        plan: layout_mod.MigrationPlan,
        sections_per_project: int = SECTIONS_PER_PROJECT
        ) -> layout_mod.MigrationPlan:
    """Move sections past the per-project cap into numbered sibling projects.

    Any task filed under a section that moves has its own project_key moved
    to match, since a task's project and its section's project must agree.
    """
    from .layout import PlannedProject, PlannedTask

    projects = list(plan.projects)
    names = {p.key: p.name for p in plan.projects}
    # As in _apply_overflow: the sibling keeps the original's folder parent.
    parents = {p.key: p.parent_key for p in plan.projects}
    seen: Dict[str, int] = {}
    rebuilt_sections = []
    extra_projects = []
    section_new_project: Dict[str, str] = {}

    for section in plan.sections:
        count = seen.get(section.project_key, 0)
        seen[section.project_key] = count + 1
        bucket = count // sections_per_project
        if bucket == 0:
            rebuilt_sections.append(section)
            continue
        overflow_key = "{0}#{1}".format(section.project_key, bucket + 1)
        if overflow_key not in {p.key for p in extra_projects}:
            extra_projects.append(PlannedProject(
                key=overflow_key,
                name="{0} ({1})".format(names.get(section.project_key,
                                                  section.project_key),
                                        bucket + 1),
                parent_key=parents.get(section.project_key),
            ))
        section_new_project[section.key] = overflow_key
        rebuilt_sections.append(replace(section, project_key=overflow_key))

    reassigned = []
    for planned in plan.tasks:
        if planned.section_key in section_new_project:
            reassigned.append(PlannedTask(
                planned.task, section_new_project[planned.section_key],
                planned.section_key))
        else:
            reassigned.append(planned)

    return layout_mod.MigrationPlan(
        plan.layout, tuple(projects + extra_projects),
        tuple(rebuilt_sections), tuple(reassigned),
    )

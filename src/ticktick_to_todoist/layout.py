"""Decides the Todoist project/section structure for an export."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .model import STATUS_COMPLETED, Task

LAYOUT_PROJECTS = "projects"
LAYOUT_SECTIONS = "sections"


@dataclass(frozen=True)
class PlannedProject:
    key: str
    name: str
    parent_key: Optional[str]


@dataclass(frozen=True)
class PlannedSection:
    key: str
    name: str
    project_key: str


@dataclass(frozen=True)
class PlannedTask:
    task: Task
    project_key: str
    section_key: Optional[str]


@dataclass(frozen=True)
class MigrationPlan:
    layout: str
    projects: Tuple[PlannedProject, ...]
    sections: Tuple[PlannedSection, ...]
    tasks: Tuple[PlannedTask, ...]


def _ordered_unique(values: Sequence[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_plan(tasks: Sequence[Task], mode: str,
               loose_project_name: str = "Imported") -> MigrationPlan:
    if mode == LAYOUT_PROJECTS:
        return _build_projects_plan(tasks)
    if mode == LAYOUT_SECTIONS:
        return _build_sections_plan(tasks, loose_project_name)
    raise ValueError("Unknown layout mode: {0!r}".format(mode))


def _build_projects_plan(tasks: Sequence[Task]) -> MigrationPlan:
    folders = _ordered_unique([t.folder for t in tasks if t.folder])
    pairs = _ordered_unique(["{0}\x00{1}".format(t.folder, t.list_name) for t in tasks])

    projects = [PlannedProject(key="folder:" + f, name=f, parent_key=None)
                for f in folders]
    for pair in pairs:
        folder, list_name = pair.split("\x00", 1)
        projects.append(PlannedProject(
            key="list:" + pair,
            name=list_name,
            parent_key=("folder:" + folder) if folder else None,
        ))

    planned = tuple(
        PlannedTask(
            task=t,
            project_key="list:{0}\x00{1}".format(t.folder, t.list_name),
            section_key=None,
        )
        for t in tasks
    )
    return MigrationPlan(LAYOUT_PROJECTS, tuple(projects), (), planned)


def _build_sections_plan(tasks: Sequence[Task],
                         loose_project_name: str) -> MigrationPlan:
    # Each folder becomes one project; lists inside it become sections.
    # Everything with no folder shares a single project.
    project_names = _ordered_unique(
        [t.folder if t.folder else loose_project_name for t in tasks]
    )
    projects = [PlannedProject(key="project:" + n, name=n, parent_key=None)
                for n in project_names]

    section_keys = _ordered_unique([
        "{0}\x00{1}".format(t.folder if t.folder else loose_project_name, t.list_name)
        for t in tasks
    ])
    sections = []
    for key in section_keys:
        project_name, list_name = key.split("\x00", 1)
        sections.append(PlannedSection(
            key="section:" + key,
            name=list_name,
            project_key="project:" + project_name,
        ))

    planned = []
    for t in tasks:
        project_name = t.folder if t.folder else loose_project_name
        planned.append(PlannedTask(
            task=t,
            project_key="project:" + project_name,
            section_key="section:{0}\x00{1}".format(project_name, t.list_name),
        ))
    return MigrationPlan(LAYOUT_SECTIONS, tuple(projects), tuple(sections),
                         tuple(planned))


def project_task_counts(plan: MigrationPlan) -> Dict[str, int]:
    """Active (non-completed) task count per project key."""
    counts = {p.key: 0 for p in plan.projects}
    for planned in plan.tasks:
        if planned.task.status == STATUS_COMPLETED:
            continue
        counts[planned.project_key] = counts.get(planned.project_key, 0) + 1
    return counts

"""Todoist plan limits, read from the API when possible."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

# These apply on every plan, per Todoist's published usage limits.
TASKS_PER_PROJECT = 300
SECTIONS_PER_PROJECT = 20
LABELS_PER_ACCOUNT = 500
LABELS_PER_TASK = 100
MAX_TITLE = 500
MAX_DESCRIPTION = 16383

# Free plan only.
FREE_MAX_PROJECTS = 5


@dataclass(frozen=True)
class Limits:
    max_projects: int = FREE_MAX_PROJECTS
    tasks_per_project: int = TASKS_PER_PROJECT
    sections_per_project: int = SECTIONS_PER_PROJECT
    labels_per_account: int = LABELS_PER_ACCOUNT
    labels_per_task: int = LABELS_PER_TASK
    max_title: int = MAX_TITLE
    max_description: int = MAX_DESCRIPTION
    assumed: bool = False
    plan_name: str = "unknown"


FREE_PLAN_ASSUMPTION = Limits(assumed=True, plan_name="free (assumed)")


def from_sync_response(payload: Dict[str, Any]) -> Limits:
    """Build Limits from a sync response containing user_plan_limits.

    Returns the Free-plan assumption unchanged when the resource is absent,
    so callers can detect that the numbers are guesses.
    """
    user_plan_limits = payload.get("user_plan_limits")
    if not user_plan_limits:
        return FREE_PLAN_ASSUMPTION
    current = user_plan_limits.get("current")
    if current is None:
        return FREE_PLAN_ASSUMPTION
    return Limits(
        max_projects=current.get("max_projects", FREE_MAX_PROJECTS),
        tasks_per_project=current.get("max_tasks", TASKS_PER_PROJECT),
        sections_per_project=current.get("max_sections", SECTIONS_PER_PROJECT),
        labels_per_account=current.get("max_labels", LABELS_PER_ACCOUNT),
        labels_per_task=LABELS_PER_TASK,
        max_title=MAX_TITLE,
        max_description=MAX_DESCRIPTION,
        assumed=False,
        plan_name=current.get("plan_name", "unknown"),
    )

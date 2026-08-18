"""Translates TickTick CSV rows into normalized Task objects."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import csvparse
from .model import (
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_NORMAL,
    Task,
)

MAX_TITLE = 500
MAX_DESCRIPTION = 16383

# TickTick 0/1/3/5 -> Todoist 1-4, where 4 is urgent. The scale is inverted.
PRIORITY_MAP = {"0": 1, "1": 2, "3": 3, "5": 4}

STATUS_MAP = {"0": STATUS_NORMAL, "2": STATUS_COMPLETED, "-1": STATUS_ABANDONED}

_FREQ_UNITS = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}
_INTERVAL_RRULE = re.compile(r"^FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)(?:;INTERVAL=(\d+))?$")


def map_priority(value: str) -> int:
    return PRIORITY_MAP.get((value or "0").strip(), 1)


def map_labels(value: str) -> Tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def to_zulu(value: str) -> Optional[str]:
    """'2026-01-01T00:00:00+0000' -> '2026-01-01T00:00:00Z'."""
    value = (value or "").strip()
    if not value:
        return None
    return re.sub(r"\+0000$", "Z", value)


def recurrence_string(repeat: str) -> Optional[str]:
    """Convert only the RRULE shapes we can be confident about.

    Anything with BYDAY, COUNT, UNTIL and friends returns None so the caller
    keeps the one-off due date and flags the task for manual review.
    """
    rule = (repeat or "").replace("RRULE:", "").strip().upper()
    if not rule:
        return None
    match = _INTERVAL_RRULE.match(rule)
    if not match:
        return None
    unit = _FREQ_UNITS[match.group(1)]
    interval = int(match.group(2) or "1")
    if interval == 1:
        return "every {0}".format(unit)
    return "every {0} {1}s".format(interval, unit)


def build_due(row: Dict[str, str], repeat_string: Optional[str]) -> Optional[Dict[str, Any]]:
    raw = (row.get("Due Date") or "").strip()
    if not raw:
        return None
    due = {}
    if (row.get("Is All Day") or "").strip().lower() == "true":
        due["date"] = raw[:10]
    else:
        due["date"] = to_zulu(raw)
        timezone = (row.get("Timezone") or "").strip()
        if timezone:
            due["timezone"] = timezone
    if repeat_string:
        due["is_recurring"] = True
        due["string"] = repeat_string
    return due


def task_from_row(row: Dict[str, str]) -> Task:
    repeat_raw = (row.get("Repeat") or "").strip()
    repeat_string = recurrence_string(repeat_raw)
    due = build_due(row, repeat_string)

    title = (row.get("Title") or "").strip() or "(untitled)"
    description = (row.get("Content") or "").strip()

    warnings = []
    if len(title) > MAX_TITLE:
        warnings.append("title is over {0} characters".format(MAX_TITLE))
    if len(description) > MAX_DESCRIPTION:
        warnings.append("description is over {0} characters".format(MAX_DESCRIPTION))
    if repeat_raw and not repeat_string:
        warnings.append("repeat rule '{0}' needs manual setup".format(repeat_raw))
    if repeat_raw and repeat_string and not due:
        warnings.append("repeat rule '{0}' has no due date to attach to".format(repeat_raw))

    status = STATUS_MAP.get((row.get("Status") or "0").strip(), STATUS_NORMAL)

    return Task(
        row_id=(row.get("taskId") or "").strip(),
        parent_row_id=(row.get("parentId") or "").strip(),
        folder=(row.get("Folder Name") or "").strip(),
        list_name=(row.get("List Name") or "").strip() or "Imported",
        title=title,
        description=description,
        labels=map_labels(row.get("Tags", "")),
        priority=map_priority(row.get("Priority", "0")),
        due=due,
        status=status,
        completed_at=to_zulu(row.get("Completed Time", "")),
        is_checklist=(row.get("Is Check list") or "").strip().upper() == "Y",
        project_kind=(row.get("projectKind") or "TASK").strip() or "TASK",
        repeat_raw=repeat_raw,
        repeat_converted=bool(repeat_string and due),
        warnings=tuple(warnings),
    )


def load_tasks(path: str) -> List[Task]:
    return [task_from_row(row) for row in csvparse.load_records(path)]

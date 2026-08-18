"""Normalized representation of a TickTick export."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

STATUS_NORMAL = "normal"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"


@dataclass(frozen=True)
class Task:
    row_id: str
    parent_row_id: str
    folder: str
    list_name: str
    title: str
    description: str
    labels: Tuple[str, ...]
    priority: int
    due: Optional[Dict[str, Any]]
    status: str
    completed_at: Optional[str]
    is_checklist: bool
    project_kind: str
    repeat_raw: str
    repeat_converted: bool
    warnings: Tuple[str, ...]

"""Records what a live run created, so it can resume or be undone."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

FORMAT_VERSION = 1


class StateError(Exception):
    """Raised when an existing state file cannot be read."""


class MigrationState:
    def __init__(self, path: str, data: Optional[Dict[str, Any]] = None):
        self.path = path
        data = data or {}
        # No self.version: the format-version check happens in load(), on the
        # raw dict, before an instance exists to hold it.
        self.projects: Dict[str, str] = data.get("projects", {})
        self.sections: Dict[str, str] = data.get("sections", {})
        # row_id -> {"id": todoist id, "project_id": where it landed}
        self.tasks: Dict[str, Dict[str, str]] = data.get("tasks", {})

    @staticmethod
    def exists(path: str) -> bool:
        return os.path.exists(path)

    @classmethod
    def load(cls, path: str) -> "MigrationState":
        if not os.path.exists(path):
            return cls(path)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as error:
            raise StateError(
                "Could not read the migration state file {0}: {1}. Delete it to "
                "start over, or point elsewhere with --state-file.".format(
                    path, error)
            )
        version = data.get("version", FORMAT_VERSION)
        if version > FORMAT_VERSION:
            raise StateError(
                "The migration state file {0} was written by a newer version "
                "of this tool (format version {1}, this build understands up "
                "to {2}). Upgrade the tool, or delete the file to start "
                "over.".format(path, version, FORMAT_VERSION)
            )
        return cls(path, data)

    # -- recording -----------------------------------------------------

    def record_project(self, key: str, todoist_id: str) -> None:
        self.projects[key] = todoist_id

    def record_section(self, key: str, todoist_id: str) -> None:
        self.sections[key] = todoist_id

    def record_task(self, row_id: str, todoist_id: str,
                    project_id: str) -> None:
        self.tasks[row_id] = {"id": todoist_id, "project_id": project_id}

    # -- lookups -------------------------------------------------------

    def project_id(self, key: str) -> Optional[str]:
        return self.projects.get(key)

    def section_id(self, key: str) -> Optional[str]:
        return self.sections.get(key)

    def task_id(self, row_id: str) -> Optional[str]:
        entry = self.tasks.get(row_id)
        return entry["id"] if entry else None

    def has_task(self, row_id: str) -> bool:
        return row_id in self.tasks

    # -- persistence ---------------------------------------------------

    def save(self) -> None:
        payload = {
            "version": FORMAT_VERSION,
            "projects": self.projects,
            "sections": self.sections,
            "tasks": self.tasks,
        }
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(temporary, self.path)

    def clear(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)

    # -- undo ----------------------------------------------------------

    def undo_commands(self) -> List[Dict[str, Any]]:
        """Delete items that live in projects we did not create, then delete
        the projects we did create -- which takes their own items with them."""
        from .sync import new_command

        created_project_ids = set(self.projects.values())
        commands = []
        for entry in self.tasks.values():
            if entry.get("project_id") in created_project_ids:
                continue
            commands.append(new_command("item_delete", {"id": entry["id"]}))
        for project_id in self.projects.values():
            commands.append(new_command("project_delete", {"id": project_id}))
        return commands

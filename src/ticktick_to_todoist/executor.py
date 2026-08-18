"""Turns a migration plan into Sync API commands and runs them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .layout import MigrationPlan, PlannedTask
from .model import STATUS_COMPLETED, Task
from .state import MigrationState
from .sync import MAX_COMMANDS_PER_REQUEST, SyncClient, new_command


@dataclass
class ExecutionResult:
    projects_created: int = 0
    sections_created: int = 0
    tasks_created: int = 0
    tasks_completed: int = 0
    skipped_existing: int = 0
    errors: List[Any] = field(default_factory=list)


def metadata_footer_for(task: Task) -> str:
    """Preserve values Todoist has nowhere to put, and only those.

    Returns "" when everything about the task mapped cleanly, so ordinary
    tasks do not pick up noise in their description.
    """
    lines = []
    if task.repeat_raw and not task.repeat_converted:
        lines.append("Original TickTick repeat rule: {0}".format(task.repeat_raw))
    if not lines:
        return ""
    return "\n\n---\nImported from TickTick\n" + "\n".join(lines)


def build_waves(tasks: Sequence[PlannedTask]) -> List[List[PlannedTask]]:
    """Group tasks so every parent is created before its children.

    Tasks whose parent is missing from the export go in the first wave and
    are imported as top-level tasks rather than being dropped. A cycle
    (rows whose parent chains loop back on themselves) can never have a
    "ready" member, so it is flushed out as one wave instead of looping
    forever.
    """
    present = {p.task.row_id for p in tasks if p.task.row_id}
    waves: List[List[PlannedTask]] = []
    placed: set = set()
    remaining = list(tasks)

    while remaining:
        wave = []
        deferred = []
        for planned in remaining:
            parent = planned.task.parent_row_id
            if not parent or parent not in present or parent in placed:
                wave.append(planned)
            else:
                deferred.append(planned)
        if not wave:
            # A cycle: emit the rest as one wave rather than loop forever.
            wave, deferred = deferred, []
        for planned in wave:
            if planned.task.row_id:
                placed.add(planned.task.row_id)
        waves.append(wave)
        remaining = deferred
    return waves


def _item_args(planned: PlannedTask, project_id: str,
               section_id: Optional[str], parent_id: Optional[str],
               metadata_footer: bool) -> Dict[str, Any]:
    task = planned.task
    args: Dict[str, Any] = {
        "content": task.title,
        "project_id": project_id,
        "priority": task.priority,
    }
    description = task.description
    if metadata_footer:
        description = (description + metadata_footer_for(task)).strip()
    if description:
        args["description"] = description
    if task.labels:
        args["labels"] = list(task.labels)
    if task.due:
        args["due"] = dict(task.due)
    if section_id:
        args["section_id"] = section_id
    if parent_id:
        args["parent_id"] = parent_id
    return args


def execute(plan: MigrationPlan, client: SyncClient, state: MigrationState,
            dry_run: bool = True, metadata_footer: bool = True,
            on_progress: Optional[Callable[[str], None]] = None
            ) -> ExecutionResult:
    result = ExecutionResult()
    notify = on_progress or (lambda _message: None)

    fake_id_counter = [0]

    def fake_id(prefix: str) -> str:
        fake_id_counter[0] += 1
        return "DRYRUN-{0}-{1}".format(prefix, fake_id_counter[0])

    def send(commands: List[Dict[str, Any]],
             label: str) -> Tuple[Dict[str, str], Set[str]]:
        """Returns (temp_id -> real id, uuids of commands that failed)."""
        if not commands:
            return {}, set()
        notify(label)
        if dry_run:
            return ({c["temp_id"]: fake_id("id") for c in commands
                     if "temp_id" in c}, set())
        batch = client.execute(commands)
        result.errors.extend(batch.errors)
        return batch.temp_id_mapping, batch.failed_uuids

    # -- projects, parents first so children can reference real ids ----
    # Each project is sent in its own request (rather than batched) because
    # a later project's parent_id must be a *real* id already recorded in
    # state -- not a temp_id, which only resolves within the request that
    # minted it. state.save() after each one means that if the process
    # dies partway through a long project list, everything already created
    # for real on Todoist is already durably on disk, so a resumed run
    # will not recreate it.
    for project in plan.projects:
        if state.project_id(project.key):
            continue
        parent_real_id = (state.project_id(project.parent_key)
                          if project.parent_key else None)
        if project.parent_key and not parent_real_id:
            # The folder project this one should hang off never got created,
            # so this project is about to become top-level. The parent's own
            # failure is already in result.errors, but nothing there says
            # this child was structurally affected -- record that explicitly
            # rather than leaving the user to infer it.
            result.errors.append(
                ("project_add", project.name,
                 "parent project failed to create; created as top-level "
                 "instead")
            )
        args: Dict[str, Any] = {"name": project.name}
        if parent_real_id:
            args["parent_id"] = parent_real_id
        temp_id = "p{0}".format(len(state.projects))
        mapping, _ = send([new_command("project_add", args, temp_id=temp_id)],
                          "project: {0}".format(project.name))
        if temp_id in mapping:
            state.record_project(project.key, mapping[temp_id])
            result.projects_created += 1
            if not dry_run:
                state.save()

    # -- sections ------------------------------------------------------
    section_commands = []
    section_keys = []
    for section in plan.sections:
        if state.section_id(section.key):
            continue
        project_id = state.project_id(section.project_key)
        if not project_id:
            continue
        temp_id = "s{0}".format(len(section_keys))
        section_keys.append((section.key, temp_id))
        section_commands.append(new_command(
            "section_add",
            {"name": section.name, "project_id": project_id},
            temp_id=temp_id,
        ))
    for chunk_start in range(0, len(section_commands), MAX_COMMANDS_PER_REQUEST):
        chunk = section_commands[chunk_start:chunk_start + MAX_COMMANDS_PER_REQUEST]
        mapping, _ = send(chunk, "sections ({0})".format(len(chunk)))
        for key, temp_id in section_keys:
            if temp_id in mapping and not state.section_id(key):
                state.record_section(key, mapping[temp_id])
                result.sections_created += 1
        if not dry_run:
            state.save()

    # -- tasks, wave by wave -------------------------------------------
    for wave_index, wave in enumerate(build_waves(list(plan.tasks))):
        pending = [p for p in wave if not state.has_task(p.task.row_id)
                   or not p.task.row_id]
        result.skipped_existing += len(wave) - len(pending)

        batch: List[Dict[str, Any]] = []
        batch_rows: List[tuple] = []

        def flush(label: str) -> None:
            if not batch:
                return
            mapping, failed_uuids = send(list(batch), label)
            # Only count a task as created/completed once Todoist has
            # actually confirmed it -- the same "temp_id in mapping" check
            # that gates recording it into state. A rejected item_add (bad
            # due date, plan limit, ...) must not inflate these counts,
            # since result.errors already lists it as a failure.
            #
            # tasks_completed additionally hinges on the *separate*
            # item_complete command for that task: an item_add can succeed
            # while its paired item_complete is rejected, and counting the
            # task as completed then would have the summary claim it was
            # closed while the error list right below said otherwise.
            for row_id, temp_id, project_id, complete_uuid in batch_rows:
                if temp_id in mapping:
                    if row_id:
                        state.record_task(row_id, mapping[temp_id], project_id)
                    result.tasks_created += 1
                    if complete_uuid and complete_uuid not in failed_uuids:
                        result.tasks_completed += 1
            del batch[:]
            del batch_rows[:]
            if not dry_run:
                state.save()

        for index, planned in enumerate(pending):
            project_id = state.project_id(planned.project_key)
            if not project_id:
                result.errors.append(
                    ("item_add", planned.task.title,
                     "its project was not created")
                )
                continue
            section_id = (state.section_id(planned.section_key)
                          if planned.section_key else None)
            parent_id = (state.task_id(planned.task.parent_row_id)
                         if planned.task.parent_row_id else None)

            temp_id = "w{0}i{1}".format(wave_index, index)
            commands = [new_command(
                "item_add",
                _item_args(planned, project_id, section_id, parent_id,
                           metadata_footer),
                temp_id=temp_id,
            )]
            complete_uuid: Optional[str] = None
            if planned.task.status == STATUS_COMPLETED:
                complete_args: Dict[str, Any] = {"id": temp_id}
                if planned.task.completed_at:
                    complete_args["date_completed"] = planned.task.completed_at
                completion = new_command("item_complete", complete_args)
                complete_uuid = completion["uuid"]
                commands.append(completion)

            # Keep an item_add and its item_complete in the same request:
            # a temp_id only resolves within the request that created it.
            if len(batch) + len(commands) > MAX_COMMANDS_PER_REQUEST:
                flush("tasks (wave {0})".format(wave_index + 1))

            batch.extend(commands)
            batch_rows.append((planned.task.row_id, temp_id, project_id,
                               complete_uuid))

        flush("tasks (wave {0})".format(wave_index + 1))

    if not dry_run:
        state.save()
    return result

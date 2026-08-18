"""An in-process stand-in for Todoist's Sync API, used as a transport."""

import json


class FakeTodoist:
    def __init__(self, project_limit=None, plan_name="pro"):
        self.projects = {}      # id -> {"name":..., "parent_id":...}
        self.sections = {}      # id -> {"name":..., "project_id":...}
        self.items = {}         # id -> args dict
        self.completed = {}     # id -> date_completed
        self.deleted = []       # ids, in deletion order
        self.requests = []
        self.project_limit = project_limit
        self.plan_name = plan_name
        self._next_id = 1000
        self._queued_status = None

    def fail_next_with(self, status):
        self._queued_status = status

    def _mint(self):
        self._next_id += 1
        return str(self._next_id)

    def transport(self, url, headers, body):
        payload = json.loads(body.decode("utf-8"))
        self.requests.append(payload)

        if self._queued_status is not None:
            status, self._queued_status = self._queued_status, None
            # Echo the Authorization header back into the error body, the
            # way a real API's error page might reflect request details.
            # This lets tests confirm the client actually redacts the token
            # rather than merely never encountering it in canned fixtures.
            detail = "forced failure; saw header: {0}".format(
                headers.get("Authorization", ""))
            return (status, {"Retry-After": "0"},
                    json.dumps({"error": detail}).encode("utf-8"))

        if payload.get("resource_types"):
            return 200, {}, json.dumps(self._read(payload)).encode("utf-8")

        return 200, {}, json.dumps(self._commands(payload)).encode("utf-8")

    def _read(self, payload):
        response = {}
        wanted = payload["resource_types"]
        if "user" in wanted:
            response["user"] = {"email": "someone@example.com", "id": "1"}
        if "user_plan_limits" in wanted:
            response["user_plan_limits"] = {"current": {
                "plan_name": self.plan_name,
                "max_projects": self.project_limit or 300,
                "max_tasks": 300, "max_sections": 20, "max_labels": 500,
            }}
        if "projects" in wanted:
            # The real resource carries these flags on every project, so a
            # test can mark one archived/deleted/Inbox simply by setting the
            # corresponding key when it seeds self.projects.
            defaults = {"is_archived": False, "is_deleted": False,
                        "inbox_project": False}
            response["projects"] = [
                dict(defaults, id=k, **v) for k, v in self.projects.items()
            ]
        return response

    def _commands(self, payload):
        commands = payload["commands"]
        assert len(commands) <= 100, "more than 100 commands in one request"
        temp_id_mapping = {}
        sync_status = {}
        for command in commands:
            kind = command["type"]
            args = command.get("args", {})
            try:
                new_id = self._apply(kind, args, temp_id_mapping)
            except _Rejected as rejection:
                sync_status[command["uuid"]] = {"error": str(rejection),
                                                "error_code": 1}
                continue
            sync_status[command["uuid"]] = "ok"
            if new_id and "temp_id" in command:
                temp_id_mapping[command["temp_id"]] = new_id
        return {"sync_status": sync_status, "temp_id_mapping": temp_id_mapping,
                "sync_token": "fake"}

    def _resolve(self, value, temp_id_mapping):
        return temp_id_mapping.get(value, value)

    def _apply(self, kind, args, temp_id_mapping):
        if kind == "project_add":
            active = len(self.projects) - len(
                [d for d in self.deleted if d in self.projects])
            if self.project_limit is not None and active >= self.project_limit:
                raise _Rejected("PROJECT_LIMIT_REACHED")
            new_id = self._mint()
            self.projects[new_id] = {
                "name": args["name"],
                "parent_id": self._resolve(args.get("parent_id"),
                                           temp_id_mapping),
            }
            return new_id
        if kind == "section_add":
            new_id = self._mint()
            self.sections[new_id] = {
                "name": args["name"],
                "project_id": self._resolve(args["project_id"],
                                            temp_id_mapping),
            }
            return new_id
        if kind == "item_add":
            new_id = self._mint()
            stored = dict(args)
            stored["project_id"] = self._resolve(args.get("project_id"),
                                                 temp_id_mapping)
            if args.get("parent_id"):
                stored["parent_id"] = self._resolve(args["parent_id"],
                                                    temp_id_mapping)
            if args.get("section_id"):
                stored["section_id"] = self._resolve(args["section_id"],
                                                     temp_id_mapping)
            self.items[new_id] = stored
            return new_id
        if kind == "item_complete":
            target = self._resolve(args["id"], temp_id_mapping)
            if target not in self.items:
                raise _Rejected("ITEM_NOT_FOUND")
            self.completed[target] = args.get("date_completed")
            return None
        if kind in ("project_delete", "item_delete"):
            target = self._resolve(args["id"], temp_id_mapping)
            self.deleted.append(target)
            self.projects.pop(target, None)
            self.items.pop(target, None)
            return None
        raise _Rejected("UNKNOWN_COMMAND:" + kind)


class _Rejected(Exception):
    pass

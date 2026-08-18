"""Todoist Sync API client built on the standard library."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .auth import redact

SYNC_URL = "https://api.todoist.com/api/v1/sync"

# Todoist accepts at most 100 commands per request, and a temp_id only
# resolves within the request that created it.
MAX_COMMANDS_PER_REQUEST = 100

USER_AGENT = "ticktick-to-todoist/0.1.0"

# Without this a stalled connection hangs the tool forever, mid-migration,
# with no way to tell it apart from a slow-but-live request.
REQUEST_TIMEOUT_SECONDS = 30


class SyncError(Exception):
    """Raised when the whole request failed, as opposed to one command."""


@dataclass
class BatchResult:
    temp_id_mapping: Dict[str, str] = field(default_factory=dict)
    # (command_type, description, error message)
    errors: List[Tuple[str, str, str]] = field(default_factory=list)
    # uuids of the commands in `errors`. A command that creates nothing
    # (item_complete, item_delete) has no temp_id to look up, so its uuid is
    # the only way for a caller to tell whether it specifically succeeded.
    failed_uuids: Set[str] = field(default_factory=set)


def new_command(command_type: str, args: Dict[str, Any],
                temp_id: Optional[str] = None) -> Dict[str, Any]:
    command = {
        "type": command_type,
        "uuid": str(uuid_module.uuid4()),
        "args": args,
    }
    if temp_id is not None:
        command["temp_id"] = temp_id
    return command


def _urllib_transport(url: str, headers: Dict[str, str],
                      body: bytes) -> Tuple[int, Dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
    try:
        with urllib.request.urlopen(request,
                                    timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        # An HTTP error is still an answer from Todoist: hand the status
        # back so _post() can retry or report it like any other status.
        return error.code, dict(error.headers or {}), error.read()
    except OSError as error:
        # No answer at all: DNS failure, refused connection, reset socket,
        # TLS problem, or the timeout above. urllib.error.URLError is itself
        # an OSError subclass, as is socket.timeout. There is no status to
        # interpret, so this is a whole-request failure -- SyncError is what
        # every caller already handles for that.
        reason = getattr(error, "reason", None) or error
        raise SyncError(redact(
            "Could not reach Todoist at {0}: {1}".format(url, reason),
            headers.get("Authorization", "").replace("Bearer ", "").strip(),
        ))


class SyncClient:
    def __init__(self, token: str,
                 transport: Optional[Callable[..., Tuple[int, Dict[str, str], bytes]]] = None,
                 pause: float = 0.3,
                 sleep: Callable[[float], None] = time.sleep,
                 max_retries: int = 3):
        self._token = token
        self._transport = transport or _urllib_transport
        self._pause = pause
        self._sleep = sleep
        self._max_retries = max_retries

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Authorization": "Bearer {0}".format(self._token),
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        body = json.dumps(payload).encode("utf-8")

        attempt = 0
        while True:
            status, response_headers, raw = self._transport(SYNC_URL, headers,
                                                            body)
            if status == 429 and attempt < self._max_retries:
                wait = float(response_headers.get("Retry-After", "5") or 5)
                self._sleep(wait)
                attempt += 1
                continue
            if status == 401:
                raise SyncError(
                    "Todoist rejected the API token (401). Check that it is "
                    "the personal token from Settings -> Integrations -> "
                    "Developer."
                )
            if status >= 400:
                if attempt < self._max_retries and status >= 500:
                    self._sleep(2 ** attempt)
                    attempt += 1
                    continue
                detail = redact(raw.decode("utf-8", "replace"),
                                self._token)[:500]
                raise SyncError(
                    "Todoist returned HTTP {0}: {1}".format(status, detail)
                )
            if self._pause:
                self._sleep(self._pause)
            return json.loads(raw.decode("utf-8"))

    def read(self, resource_types: Sequence[str]) -> Dict[str, Any]:
        return self._post({
            "sync_token": "*",
            "resource_types": list(resource_types),
        })

    def execute(self, commands: Sequence[Dict[str, Any]]) -> BatchResult:
        if not commands:
            return BatchResult()
        if len(commands) > MAX_COMMANDS_PER_REQUEST:
            raise ValueError(
                "{0} commands exceeds Todoist's limit of {1} per request".format(
                    len(commands), MAX_COMMANDS_PER_REQUEST)
            )

        data = self._post({"commands": list(commands)})
        result = BatchResult(temp_id_mapping=data.get("temp_id_mapping") or {})
        statuses = data.get("sync_status") or {}
        for command in commands:
            status = statuses.get(command["uuid"])
            if status == "ok" or status is None:
                continue
            args = command.get("args", {})
            description = args.get("content") or args.get("name") or str(args)
            # A rejection dict without an "error" key would otherwise render
            # as the literal string "None", destroying the only diagnostic
            # the user gets; fall back to the dict's own repr.
            message = (status.get("error") or str(status)
                       if isinstance(status, dict) else str(status))
            result.errors.append((command["type"], description, message))
            result.failed_uuids.add(command["uuid"])
        return result

"""Resolves the Todoist API token without ever exposing it on a command line."""

from __future__ import annotations

import getpass
import os
from typing import Callable, Dict, Optional

ENV_VAR = "TODOIST_API_TOKEN"

PROMPT_TEXT = "Todoist API token (input hidden): "


class TokenError(Exception):
    """Raised when a token source was named but could not be read."""


def resolve_token(token_file: Optional[str] = None,
                  environ: Optional[Dict[str, str]] = None,
                  allow_prompt: bool = True,
                  prompt: Optional[Callable[[str], str]] = None
                  ) -> Optional[str]:
    """First match wins: --token-file, then the env var, then a hidden prompt.

    Returns None when no source yields a token. Raises TokenError only when a
    source was explicitly named and turned out to be unusable, since silently
    ignoring a bad --token-file would be worse than failing.
    """
    if environ is None:
        environ = dict(os.environ)

    if token_file:
        try:
            with open(token_file, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except (OSError, UnicodeDecodeError) as error:
            # A token file that isn't UTF-8 is just as unusable as one that
            # cannot be opened, and must fail the same clean way rather than
            # escaping as an uncaught UnicodeDecodeError.
            raise TokenError(
                "Could not read the token file {0!r}: {1}".format(
                    token_file, error)
            )
        for line in lines:
            if line.strip():
                return line.strip()
        raise TokenError(
            "The token file {0!r} is empty.".format(token_file)
        )

    from_env = (environ.get(ENV_VAR) or "").strip()
    if from_env:
        return from_env

    if allow_prompt:
        reader = prompt or getpass.getpass
        typed = (reader(PROMPT_TEXT) or "").strip()
        return typed or None

    return None


def redact(text: str, token: Optional[str]) -> str:
    """Strip the token out of anything about to be printed."""
    if not token:
        return text
    return text.replace(token, "<redacted>")

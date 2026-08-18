import pytest

from ticktick_to_todoist import auth


def test_token_file_wins_over_environment(tmp_path):
    path = tmp_path / "token"
    path.write_text("from-file\n", encoding="utf-8")
    token = auth.resolve_token(token_file=str(path),
                               environ={"TODOIST_API_TOKEN": "from-env"},
                               allow_prompt=False)
    assert token == "from-file"


def test_token_file_skips_blank_leading_lines(tmp_path):
    path = tmp_path / "token"
    path.write_text("\n\n  abc123  \n", encoding="utf-8")
    assert auth.resolve_token(token_file=str(path), environ={},
                              allow_prompt=False) == "abc123"


def test_empty_token_file_raises(tmp_path):
    path = tmp_path / "token"
    path.write_text("\n\n", encoding="utf-8")
    with pytest.raises(auth.TokenError):
        auth.resolve_token(token_file=str(path), environ={}, allow_prompt=False)


def test_missing_token_file_raises(tmp_path):
    with pytest.raises(auth.TokenError):
        auth.resolve_token(token_file=str(tmp_path / "nope"), environ={},
                           allow_prompt=False)


def test_non_utf8_token_file_raises_token_error(tmp_path):
    # A binary or latin-1 file is as unusable as an unreadable one, and must
    # fail the same clean way rather than as a raw UnicodeDecodeError.
    path = tmp_path / "token"
    path.write_bytes(b"\xff\xfe not utf-8 \x80\n")
    with pytest.raises(auth.TokenError):
        auth.resolve_token(token_file=str(path), environ={}, allow_prompt=False)


def test_environment_variable_is_used_when_no_file():
    assert auth.resolve_token(environ={"TODOIST_API_TOKEN": " env-token "},
                              allow_prompt=False) == "env-token"


def test_environment_wins_over_prompt():
    assert auth.resolve_token(environ={"TODOIST_API_TOKEN": "env-token"},
                              allow_prompt=True,
                              prompt=lambda _: "typed-token") == "env-token"


def test_prompt_is_used_as_a_last_resort():
    token = auth.resolve_token(environ={}, allow_prompt=True,
                               prompt=lambda _: "typed-token")
    assert token == "typed-token"


def test_returns_none_when_nothing_is_available_and_prompting_is_off():
    assert auth.resolve_token(environ={}, allow_prompt=False) is None


def test_blank_prompt_response_returns_none():
    assert auth.resolve_token(environ={}, allow_prompt=True,
                              prompt=lambda _: "   ") is None


def test_redact_replaces_every_occurrence():
    text = "Authorization: Bearer sekret and again sekret"
    assert "sekret" not in auth.redact(text, "sekret")
    assert auth.redact(text, "sekret").count("<redacted>") == 2


def test_redact_is_a_noop_without_a_token():
    assert auth.redact("nothing here", None) == "nothing here"

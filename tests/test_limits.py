from ticktick_to_todoist import limits


def test_free_plan_assumption_is_marked_assumed():
    assert limits.FREE_PLAN_ASSUMPTION.assumed is True
    assert limits.FREE_PLAN_ASSUMPTION.max_projects == 5


def test_universal_limits_are_the_documented_values():
    free = limits.FREE_PLAN_ASSUMPTION
    assert free.tasks_per_project == 300
    assert free.sections_per_project == 20
    assert free.labels_per_account == 500
    assert free.labels_per_task == 100
    assert free.max_title == 500
    assert free.max_description == 16383


def test_parses_current_plan_limits_from_a_sync_response():
    payload = {
        "user_plan_limits": {
            "current": {
                "plan_name": "pro",
                "max_projects": 300,
                "max_tasks": 300,
                "max_sections": 20,
                "max_labels": 500,
            }
        }
    }
    parsed = limits.from_sync_response(payload)
    assert parsed.max_projects == 300
    assert parsed.plan_name == "pro"
    assert parsed.assumed is False


def test_missing_fields_fall_back_to_documented_defaults():
    parsed = limits.from_sync_response({"user_plan_limits": {"current": {}}})
    assert parsed.max_projects == 5
    assert parsed.tasks_per_project == 300
    assert parsed.assumed is False


def test_absent_user_plan_limits_returns_the_assumption():
    assert limits.from_sync_response({}) is limits.FREE_PLAN_ASSUMPTION


def test_explicit_zero_values_are_preserved():
    payload = {
        "user_plan_limits": {
            "current": {
                "plan_name": "zero_plan",
                "max_projects": 0,
                "max_tasks": 0,
                "max_sections": 0,
                "max_labels": 0,
            }
        }
    }
    parsed = limits.from_sync_response(payload)
    assert parsed.max_projects == 0
    assert parsed.tasks_per_project == 0
    assert parsed.sections_per_project == 0
    assert parsed.labels_per_account == 0
    assert parsed.assumed is False

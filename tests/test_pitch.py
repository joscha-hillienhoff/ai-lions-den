from backend.pitch import (
    pitch_is_complete,
    validate_defense,
    validate_pitch,
    validate_pitch_draft,
    validate_team_name,
)

from .helpers import COMPLETE_PITCH


def _payload() -> dict[str, object]:
    return COMPLETE_PITCH.model_dump(by_alias=True)


def test_accepts_a_complete_pitch_inside_every_limit() -> None:
    assert validate_pitch(_payload()).ok is True


def test_rejects_missing_fields_and_over_limit_fields_on_submit() -> None:
    missing = validate_pitch({**_payload(), "problem": " "})
    assert missing.ok is False
    assert "required" in (missing.errors or {})["problem"]

    too_long = validate_pitch({**_payload(), "tagline": "x" * 141})
    assert too_long.ok is False
    assert "140" in (too_long.errors or {})["tagline"]


def test_allows_empty_fields_in_a_draft_but_still_enforces_limits() -> None:
    draft = validate_pitch_draft({"startupName": "Loopline", "tagline": ""})
    assert draft.ok is True
    assert draft.pitch is not None
    assert draft.pitch.startup_name == "Loopline"
    assert draft.pitch.problem == ""

    too_long = validate_pitch_draft({"problem": "x" * 2001})
    assert too_long.ok is False


def test_normalises_line_endings_and_strips_control_characters() -> None:
    result = validate_pitch({**_payload(), "problem": "Line one\r\nLine two\u0007"})
    assert result.ok is True
    assert result.pitch is not None
    assert result.pitch.problem == "Line one\nLine two"


def test_requires_all_four_defense_answers_each_at_most_1000_characters() -> None:
    change = "Narrow the first customer to independent pharmacies."
    valid = validate_defense({"answers": ["a", "b", "c", "d"], "oneChange": change})
    assert valid.ok is True
    assert valid.one_change == change
    missing = validate_defense({"answers": ["a", "", "c", "d"], "oneChange": change})
    assert missing.ok is False
    too_long = validate_defense({"answers": ["a", "b", "c", "x" * 1001], "oneChange": change})
    assert too_long.ok is False


def test_validates_team_names() -> None:
    result = validate_team_name("  The Margin   Calls ")
    assert result.ok is True and result.name == "The Margin Calls"
    assert validate_team_name("   ").ok is False
    assert validate_team_name("x" * 41).ok is False
    assert pitch_is_complete(COMPLETE_PITCH) is True


def test_requires_and_bounds_the_one_change_field() -> None:
    missing = validate_defense({"answers": ["a", "b", "c", "d"], "oneChange": "  "})
    assert missing.ok is False
    assert missing.errors == {
        "oneChange": "Describe the one change you make because of the criticism."
    }

    too_long = validate_defense({"answers": ["a", "b", "c", "d"], "oneChange": "x" * 1001})
    assert too_long.ok is False
    assert "1000" in (too_long.errors or {})["oneChange"]

    at_limit = validate_defense({"answers": ["a", "b", "c", "d"], "oneChange": "x" * 1000})
    assert at_limit.ok is True
    assert at_limit.one_change == "x" * 1000


def test_tolerates_older_stored_defense_without_the_change_field() -> None:
    from backend.state import Defense

    legacy = Defense.model_validate(
        {"answers": ["a", "b", "c", "d"], "submittedAt": 1_700_000_000_000}
    )
    assert legacy.one_change == ""
    assert legacy.submitted_at == 1_700_000_000_000

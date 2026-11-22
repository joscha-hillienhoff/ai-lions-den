import pytest

from backend.jury.validation import (
    JuryOutputError,
    parse_json_content,
    parse_round1_output,
    parse_verdict_output,
)


def test_accepts_a_valid_round_one_object() -> None:
    result = parse_round1_output(
        {
            "score": 7,
            "assessment": "A strong wedge. The market path is still thin.",
            "question": "What gets you the first thousand customers?",
        }
    )
    assert result.score == 7
    assert result.assessment == "A strong wedge. The market path is still thin."
    assert result.question == "What gets you the first thousand customers?"


def test_rejects_out_of_range_fractional_or_missing_scores() -> None:
    base = {"assessment": "Fine.", "question": "Why?"}
    for score in (0, 11, 7.5, "7"):
        with pytest.raises(JuryOutputError):
            parse_round1_output({**base, "score": score})
    with pytest.raises(JuryOutputError):
        parse_round1_output(base)


def test_accepts_round_one_questions_with_any_question_mark_count() -> None:
    for question in ("What is the plan? Why now?", "What gets you the first customers"):
        result = parse_round1_output(
            {
                "score": 7,
                "assessment": "A strong wedge. The market path is still thin.",
                "question": question,
            }
        )
        assert result.question == question


def test_accepts_text_with_one_or_six_sentences() -> None:
    for text in ("One sentence.", "One. Two. Three. Four. Five. Six."):
        round1 = parse_round1_output({"score": 7, "assessment": text, "question": "Why?"})
        assert round1.assessment == text
        verdict = parse_verdict_output({"statement": text, "score": 8})
        assert verdict.statement == text


def test_rejects_empty_text() -> None:
    with pytest.raises(JuryOutputError):
        parse_round1_output({"score": 7, "assessment": "  ", "question": "Why?"})
    with pytest.raises(JuryOutputError):
        parse_round1_output(
            {"score": 7, "assessment": "Strong. The wedge is clear.", "question": " "}
        )
    with pytest.raises(JuryOutputError):
        parse_verdict_output({"statement": "  ", "score": 8})


def test_normalises_whitespace_and_rejects_overlong_text() -> None:
    result = parse_verdict_output(
        {
            "statement": "First sentence.   Second sentence.",
            "score": 9,
            "decision": "in",
        }
    )
    assert result.statement == "First sentence. Second sentence."
    with pytest.raises(JuryOutputError):
        parse_verdict_output({"statement": f"First sentence. {'word ' * 180}", "score": 9})
    with pytest.raises(JuryOutputError):
        parse_round1_output(
            {
                "score": 7,
                "assessment": f"Strong wedge. {'word ' * 180}",
                "question": "Why?",
            }
        )
    with pytest.raises(JuryOutputError):
        parse_round1_output(
            {
                "score": 7,
                "assessment": "Strong wedge. The market path is thin.",
                "question": "Why? " * 101,
            }
        )


def test_accepts_classroom_length_assessments_statements_and_questions() -> None:
    assessment = (
        "Your pricing is concrete and the first market is plausible. The onboarding fee "
        "still hides the integration cost, which weakens the margin case."
    )
    statement = (
        "You answered my question with a concrete assumption, and your one change makes the "
        "pricing easier to test. The unit economics now hold together, so my score rises "
        "because the plan is more believable than in round one."
    )
    question = "Which three cost lines does the onboarding fee cover at the first customer?"

    round1 = parse_round1_output({"score": 7, "assessment": assessment, "question": question})
    assert round1.assessment == assessment
    assert round1.question == question

    verdict = parse_verdict_output({"statement": statement, "score": 8})
    assert verdict.statement == statement


def test_rejects_malformed_json_content() -> None:
    with pytest.raises(JuryOutputError):
        parse_json_content("not json")
    with pytest.raises(JuryOutputError):
        parse_json_content("")
    assert parse_json_content('{"score":7}') == {"score": 7}

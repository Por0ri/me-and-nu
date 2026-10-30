import pytest
from pydantic import ValidationError

from app.schemas.onboarding import OnboardingAnswers, OnboardingRequest


@pytest.mark.parametrize("answers", [
    {"q1": "world", "q2": "releases"},
    # The same choice ID can legitimately occur once in each question.
    {"q1": "events", "q2": "events"},
])
def test_each_question_accepts_one_choice(answers):
    assert OnboardingAnswers.model_validate(answers).model_dump() == answers


@pytest.mark.parametrize("question", ["q1", "q2"])
@pytest.mark.parametrize("invalid", [
    None, "", " ", [], ["events"], ["events", "events"],
    ["world", "events"], {"value": "events"}, 1, True,
])
def test_each_question_rejects_missing_or_non_scalar_choice(question, invalid):
    with pytest.raises(ValidationError) as caught:
        OnboardingAnswers.model_validate({
            "q1": "world", "q2": "releases", question: invalid,
        })
    assert question in {error["loc"][0] for error in caught.value.errors()}


@pytest.mark.parametrize("missing", ["q1", "q2"])
def test_both_questions_are_required(missing):
    answers = {"q1": "world", "q2": "releases"}
    del answers[missing]
    with pytest.raises(ValidationError) as caught:
        OnboardingAnswers.model_validate(answers)
    assert caught.value.errors()[0]["loc"] == (missing,)
    assert caught.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize("question, choice", [
    ("q1", "releases"), ("q2", "world"), ("q1", "unknown"), ("q2", "unknown"),
])
def test_choice_must_belong_to_its_question(question, choice):
    with pytest.raises(ValidationError):
        OnboardingAnswers.model_validate({
            "q1": "world", "q2": "releases", question: choice,
        })


def test_extra_question_is_not_silently_ignored():
    with pytest.raises(ValidationError):
        OnboardingAnswers.model_validate({
            "q1": "world", "q2": "releases", "q3": "events",
        })


def signup_payload(**changes):
    return {
        "nickname": "일반 회원",
        "accountType": "user",
        "topicId": 1,
        "answers": {"q1": "world", "q2": "releases"},
        "consents": [{"type": "terms", "policyVersion": "v1", "agreed": True}],
        **changes,
    }


def test_signup_requires_and_preserves_both_answers():
    payload = OnboardingRequest.model_validate(signup_payload())
    assert payload.answers.model_dump() == {"q1": "world", "q2": "releases"}
    missing_answers = signup_payload()
    del missing_answers["answers"]
    with pytest.raises(ValidationError) as caught:
        OnboardingRequest.model_validate(missing_answers)
    assert caught.value.errors()[0]["loc"] == ("answers",)
    assert caught.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize("answers", [
    None, {}, {"q1": "world"}, {"q2": "releases"},
    {"q1": ["world"], "q2": "releases"},
    {"q1": "world", "q2": ["releases", "events"]},
    {"q1": "releases", "q2": "world"},
])
def test_signup_validates_nested_answers(answers):
    with pytest.raises(ValidationError) as caught:
        OnboardingRequest.model_validate(signup_payload(answers=answers))
    assert all(error["loc"][0] == "answers" for error in caught.value.errors())


@pytest.mark.parametrize("extra", [
    {"subtopicIds": []}, {"subtopicIds": [11, 12]},
    {"q1": "world", "q2": "releases"},
])
def test_signup_rejects_legacy_subtopics_and_misplaced_answers(extra):
    with pytest.raises(ValidationError) as caught:
        OnboardingRequest.model_validate(signup_payload(**extra))
    assert {error["loc"][0] for error in caught.value.errors()} == set(extra)
    assert {error["type"] for error in caught.value.errors()} == {"extra_forbidden"}

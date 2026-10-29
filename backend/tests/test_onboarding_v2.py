"""V2 contracts and actual ORM/HTTP flow on a disposable SQLite database.

External OAuth is a fixture double. PostgreSQL locking and migration semantics
are covered separately; these tests never connect to the configured database.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import Integer, JSON, MetaData, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    ConsentHistory, Entity, EntityTag, NotificationSetting, OnboardingAnswersRecord,
    PreferenceTag, Tap, TapEntity, TapPreferenceTag, TapTopic, Topic, UserAccount,
)
from app.schemas.onboarding import OnboardingAnswersV2, OnboardingRequest, PreferenceOptionsRequest
from app.services.cursor import encode_cursor
from test_oauth_postgres_flow import (
    onboarding_flow, pending_onboarding, postgres_flow, signup_request,
)


def v2_answers(**changes):
    return {
        "version": 2, "favoriteEntityIds": [11, 12], "preferenceTagIds": [101, 103],
        "consumptionType": "world", **changes,
    }


def payload(answers):
    return {
        "nickname": "신규 회원", "accountType": "user", "topicId": 1, "answers": answers,
        "consents": [{"type": "terms", "policyVersion": "v1", "agreed": True}],
    }


@pytest.mark.parametrize("field,invalid", [
    ("favoriteEntityIds", []), ("favoriteEntityIds", [1, 2, 3, 4, 5, 6]),
    ("favoriteEntityIds", [1, 1]), ("favoriteEntityIds", [True]),
    ("favoriteEntityIds", ["11"]), ("favoriteEntityIds", [11.0]),
    ("favoriteEntityIds", [0]), ("favoriteEntityIds", [-1]),
    ("favoriteEntityIds", [2**63]), ("preferenceTagIds", []),
    ("preferenceTagIds", [101]),
    ("preferenceTagIds", [101, 101]), ("preferenceTagIds", ["101"]),
    ("preferenceTagIds", [False]), ("preferenceTagIds", [0]),
    ("consumptionType", []), ("consumptionType", "releases"),
    ("consumptionType", None), ("version", 1), ("version", "2"),
    ("version", 2.0), ("version", None),
])
def test_v2_rejects_invalid_values_with_clean_field_locations(field, invalid):
    with pytest.raises(ValidationError) as caught:
        OnboardingRequest.model_validate(payload(v2_answers(**{field: invalid})))
    assert all(error["loc"][:2] == ("answers", field) for error in caught.value.errors())


@pytest.mark.parametrize("field", ["favoriteEntityIds", "preferenceTagIds", "consumptionType"])
def test_v2_requires_each_answer(field):
    answers = v2_answers()
    del answers[field]
    with pytest.raises(ValidationError) as caught:
        OnboardingRequest.model_validate(payload(answers))
    assert caught.value.errors()[0]["loc"] == ("answers", field)


@pytest.mark.parametrize("answers", [
    v2_answers(q1="world", q2="releases"),
    {"q1": "world", "q2": "releases", "favoriteEntityIds": [11]},
    {key: value for key, value in v2_answers().items() if key != "version"},
])
def test_v1_v2_mixed_or_implicit_v2_answers_are_rejected(answers):
    with pytest.raises(ValidationError):
        OnboardingRequest.model_validate(payload(answers))


def test_v1_shape_and_v2_schema_defer_per_kind_cap_to_catalogue():
    legacy = {"q1": "world", "q2": "releases"}
    assert OnboardingRequest.model_validate(payload(legacy)).answers.model_dump(by_alias=True) == legacy
    tags = list(range(1, 151))
    value = OnboardingAnswersV2.model_validate(v2_answers(preferenceTagIds=tags))
    assert value.preference_tag_ids == tags


@pytest.mark.parametrize("topic_id", [True, False, "1", 1.0, 0, -1, 2**63])
@pytest.mark.parametrize("answers", [{"q1": "world", "q2": "releases"}, v2_answers()])
def test_completion_topic_id_requires_a_strict_positive_bigint(topic_id, answers):
    with pytest.raises(ValidationError) as caught:
        OnboardingRequest.model_validate({**payload(answers), "topicId": topic_id})
    assert caught.value.errors()[0]["loc"] == ("topicId",)


@pytest.mark.parametrize("entity_ids", [[], [11, 11], [11, 12, 13, 14, 15, 16], [True], ["11"]])
def test_candidate_request_requires_unique_strict_ids(entity_ids):
    with pytest.raises(ValidationError):
        PreferenceOptionsRequest.model_validate({"topicId": 1, "entityIds": entity_ids})


@pytest.fixture
def v2_flow(onboarding_flow):
    flow = onboarding_flow
    metadata = MetaData()
    # Existing table copies resolve composite FKs without changing original
    # model metadata. Only new catalogue/selection tables are created here.
    for model in (UserAccount, Topic, Tap, Entity, PreferenceTag, EntityTag, TapEntity, TapPreferenceTag):
        table = model.__table__.to_metadata(metadata)
        for column in table.primary_key.columns:
            if len(table.primary_key.columns) == 1 and isinstance(column.type, Integer):
                column.type = Integer()
        if model is Entity:
            table.c.aliases.type = JSON()
            table.c.aliases.server_default = None
    metadata.create_all(flow.engine)
    with Session(flow.engine) as session:
        session.add_all([
            Topic(topic_id=2, topic_code="other", topic_name="다른 분야", is_active=True),
            Topic(topic_id=3, topic_code="inactive", topic_name="비활성", is_active=False),
        ])
        session.flush()
        for entity_id, topic_id, name, aliases, active in (
            (11, 1, "Hero Academy", ["영웅 학교", "Alias Match"], True),
            (12, 1, "Hero%_Story", [], True),
            (13, 1, "Hero Untagged", [], True),
            (14, 1, "Hero Inactive", [], False),
            (15, 1, "Alternate Hero", [], True),
            (16, 1, "Final Hero", [], True),
            (17, 1, "Other Hero", [], True),
            (21, 2, "Hero Other Topic", [], True),
        ):
            session.add(Entity(
                entity_id=entity_id, topic_id=topic_id, name=name, aliases=aliases,
                is_active=active, release_year=2020, media_format="TV",
            ))
        for tag_id, topic_id, kind, code, label, order, active in (
            (101, 1, "genre", "action", "액션", 2, True),
            (102, 1, "genre", "drama", "드라마", 1, True),
            (103, 1, "theme", "school", "학원", 1, True),
            (104, 1, "theme", "inactive", "비활성", 2, False),
            (105, 1, "genre", "unrelated", "후보 아님", 3, True),
            (201, 2, "genre", "action", "다른 분야 액션", 1, True),
        ):
            session.add(PreferenceTag(
                tag_id=tag_id, topic_id=topic_id, kind=kind, code=code,
                display_name=label, sort_order=order, is_active=active,
            ))
        session.flush()
        for entity_id, tag_id, topic_id in (
            (11, 101, 1), (11, 103, 1), (11, 104, 1),
            (12, 101, 1), (12, 102, 1), (14, 101, 1),
            (15, 101, 1), (16, 101, 1), (17, 101, 1), (21, 201, 2),
        ):
            session.add(EntityTag(entity_id=entity_id, tag_id=tag_id, topic_id=topic_id))
        session.commit()
    return flow


def candidates(flow, csrf, entity_ids=None, topic_id=1):
    return flow.client.post(
        "/api/v1/onboarding/preference-options",
        json={"topicId": topic_id, "entityIds": entity_ids if entity_ids is not None else [11, 12]},
        headers={"X-CSRF-Token": csrf},
    )


def assert_no_onboarding_writes(flow):
    with Session(flow.engine) as session:
        user = session.scalar(select(UserAccount))
        if user is not None:
            assert user.nickname == "회원" and user.onboarding_completed_at is None
        for model in (Tap, TapEntity, TapPreferenceTag, OnboardingAnswersRecord, ConsentHistory, NotificationSetting):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_search_and_candidates_require_pending_authentication_and_post_csrf(v2_flow):
    flow = v2_flow
    assert flow.client.get("/api/v1/onboarding/works?topicId=1&q=hero").status_code == 401
    assert candidates(flow, "invalid").status_code == 401
    csrf, token = pending_onboarding(flow)
    assert candidates(flow, "invalid").status_code == 403
    assert candidates(flow, csrf).status_code == 200
    assert flow.client.cookies.get(settings.session_cookie_name) == token
    assert_no_onboarding_writes(flow)
    assert signup_request(flow, csrf, {"q1": "world", "q2": "releases"}).status_code == 201
    assert flow.client.get("/api/v1/onboarding/works?topicId=1&q=hero").status_code == 409


@pytest.mark.parametrize("query,expected", [
    (None, []), ("   ", []), ("not present", []), ("ALIAS match", [11]),
    ("영웅", [11]), ("%_", [12]), ("Inactive", []),
])
def test_search_handles_blank_aliases_literal_wildcards_and_inactive_rows(v2_flow, query, expected):
    flow = v2_flow
    pending_onboarding(flow)
    params = {"topicId": 1}
    if query is not None:
        params["q"] = query
    response = flow.client.get("/api/v1/onboarding/works", params=params)
    assert response.status_code == 200, response.text
    assert [row["entityId"] for row in response.json()["items"]] == expected
    assert response.json()["nextCursor"] is None
    assert_no_onboarding_writes(flow)


def test_search_pagination_is_stable_scoped_and_lets_untagged_works_be_chosen(v2_flow):
    flow = v2_flow
    pending_onboarding(flow)
    first = flow.client.get("/api/v1/onboarding/works", params={"topicId": 1, "q": "hero", "limit": 2})
    assert first.status_code == 200, first.text
    assert [row["entityId"] for row in first.json()["items"]] == [11, 12]
    cursor = first.json()["nextCursor"]
    second = flow.client.get("/api/v1/onboarding/works", params={"topicId": 1, "q": " HERO ", "limit": 2, "cursor": cursor})
    assert [row["entityId"] for row in second.json()["items"]] == [13, 15]
    untagged = second.json()["items"][0]
    assert untagged["selectable"] is True and untagged["unavailableReason"] is None
    for changed in ({"topicId": 2, "q": "hero"}, {"topicId": 1, "q": "alias"}):
        result = flow.client.get("/api/v1/onboarding/works", params={**changed, "cursor": cursor})
        assert result.status_code == 422 and result.json()["code"] == "INVALID_CURSOR"
    invalid_cursor = encode_cursor({"v": 1, "resource": "onboarding-works", "topicId": 1, "q": "hero", "after": True})
    assert flow.client.get("/api/v1/onboarding/works", params={"topicId": 1, "q": "hero", "cursor": invalid_cursor}).status_code == 422
    for limit in (0, 51):
        assert flow.client.get("/api/v1/onboarding/works", params={"topicId": 1, "q": "hero", "limit": limit}).status_code == 422


def test_options_list_every_active_chip_with_work_sources_and_are_read_only(v2_flow):
    flow = v2_flow
    csrf, _ = pending_onboarding(flow)
    response = candidates(flow, csrf)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entityIds"] == [11, 12]
    # Every active chip in display order; inactive 104 and other-Topic 201 stay out.
    assert [tag["tagId"] for tag in body["genres"]] == [102, 101, 105]
    assert [tag["tagId"] for tag in body["themes"]] == [103]
    assert [tag["sourceEntityIds"] for tag in body["genres"]] == [[12], [11, 12], []]
    assert body["themes"][0]["sourceEntityIds"] == [11]
    assert body["minSelections"] == 2 and body["maxSelections"] is None
    assert body["minGenreSelections"] == 1 and body["minThemeSelections"] == 1
    assert body["maxSelectionsPerKind"] == 7
    assert_no_onboarding_writes(flow)


def test_options_accept_a_work_without_chips(v2_flow):
    flow = v2_flow
    csrf, _ = pending_onboarding(flow)
    response = candidates(flow, csrf, [13])
    assert response.status_code == 200, response.text
    options = response.json()["genres"] + response.json()["themes"]
    assert [tag["tagId"] for tag in options] == [102, 101, 105, 103]
    assert all(tag["sourceEntityIds"] == [] for tag in options)


@pytest.mark.parametrize("entity_ids", [[999], [14], [21], [11, 11]])
def test_candidate_rejects_unknown_inactive_other_topic_and_duplicate_works(v2_flow, entity_ids):
    flow = v2_flow
    csrf, _ = pending_onboarding(flow)
    response = candidates(flow, csrf, entity_ids)
    assert response.status_code == 422, response.text
    assert response.json()["fields"][0]["field"] == "entityIds"
    assert_no_onboarding_writes(flow)


@pytest.mark.parametrize("topic_id", [3, 999])
def test_search_candidates_and_completion_keep_topic_not_found(v2_flow, topic_id):
    flow = v2_flow
    csrf, _ = pending_onboarding(flow)
    for response in (
        flow.client.get("/api/v1/onboarding/works", params={"topicId": topic_id, "q": "hero"}),
        candidates(flow, csrf, topic_id=topic_id),
        signup_request(flow, csrf, v2_answers(), topicId=topic_id),
    ):
        assert response.status_code == 404 and response.json()["code"] == "TOPIC_NOT_FOUND"
    assert_no_onboarding_writes(flow)


@pytest.mark.parametrize("consumption_type", ["world", "relationships", "ost", "goods", "events", "news"])
def test_v2_completion_atomically_persists_selections_and_rotates_session(v2_flow, consumption_type):
    flow = v2_flow
    csrf, pending_token = pending_onboarding(flow)
    response = signup_request(flow, csrf, v2_answers(consumptionType=consumption_type))
    assert response.status_code == 201, response.text
    assert response.json()["answers"] == v2_answers(consumptionType=consumption_type)
    assert response.json()["activeTopic"]["subtopicIds"] == []
    assert flow.client.cookies.get(settings.session_cookie_name) != pending_token
    assert flow.client.get("/api/v1/auth/session").json()["sessionState"] == "active"
    with Session(flow.engine) as session:
        answers = session.scalar(select(OnboardingAnswersRecord))
        assert (answers.schema_version, answers.q1_choice, answers.q2_choice, answers.consumption_type) == (2, None, None, consumption_type)
        assert set(session.scalars(select(TapEntity.entity_id))) == {11, 12}
        assert set(session.scalars(select(TapPreferenceTag.tag_id))) == {101, 103}
        assert session.scalar(select(func.count()).select_from(TapTopic)) == 0
        assert session.scalar(select(func.count()).select_from(ConsentHistory)) == 3
    assert signup_request(flow, csrf, v2_answers()).status_code == 409


def add_chips(flow, kind, tag_ids):
    with Session(flow.engine) as session:
        for tag_id in tag_ids:
            session.add(PreferenceTag(tag_id=tag_id, topic_id=1, kind=kind, code=f"tag-{tag_id}", display_name=f"칩 {tag_id}", is_active=True))
        session.commit()


def test_seven_chips_per_kind_and_five_works_can_be_selected(v2_flow):
    flow = v2_flow
    add_chips(flow, "genre", range(300, 304))
    add_chips(flow, "theme", range(400, 406))
    csrf, _ = pending_onboarding(flow)
    selected = [11, 12, 15, 16, 17]
    options = candidates(flow, csrf, selected).json()
    assert (len(options["genres"]), len(options["themes"])) == (7, 7)
    all_tags = [option["tagId"] for option in options["genres"] + options["themes"]]
    response = signup_request(flow, csrf, v2_answers(favoriteEntityIds=selected, preferenceTagIds=all_tags))
    assert response.status_code == 201, response.text
    assert set(response.json()["answers"]["preferenceTagIds"]) == set(all_tags)


@pytest.mark.parametrize("kind", ["genre", "theme"])
def test_more_than_seven_chips_of_one_kind_are_rejected(v2_flow, kind):
    flow = v2_flow
    add_chips(flow, kind, range(300, 308))
    csrf, token = pending_onboarding(flow)
    opposite_kind = 103 if kind == "genre" else 101
    response = signup_request(flow, csrf, v2_answers(preferenceTagIds=[*range(300, 308), opposite_kind]))
    assert response.status_code == 422, response.text
    assert response.json()["fields"][0]["field"] == "answers.preferenceTagIds"
    assert flow.client.cookies.get(settings.session_cookie_name) == token
    assert_no_onboarding_writes(flow)


def test_one_work_with_one_genre_and_one_theme_satisfies_minimum(v2_flow):
    flow = v2_flow
    csrf, _ = pending_onboarding(flow)
    response = signup_request(
        flow, csrf, v2_answers(favoriteEntityIds=[11], preferenceTagIds=[101, 103]),
    )
    assert response.status_code == 201, response.text
    assert response.json()["answers"]["favoriteEntityIds"] == [11]
    assert response.json()["answers"]["preferenceTagIds"] == [101, 103]
    with Session(flow.engine) as session:
        assert session.scalar(select(func.count()).select_from(TapEntity)) == 1
        assert session.scalar(select(func.count()).select_from(TapPreferenceTag)) == 2


@pytest.mark.parametrize("tag_ids", [[], [101], [103], [101, 102], [103, 106]])
def test_missing_preference_kind_rejects_without_partial_onboarding(v2_flow, tag_ids):
    flow = v2_flow
    if 106 in tag_ids:
        add_chips(flow, "theme", [106])
    csrf, token = pending_onboarding(flow)
    response = signup_request(flow, csrf, v2_answers(preferenceTagIds=tag_ids))
    assert response.status_code == 422, response.text
    assert response.json()["fields"][0]["field"] == "answers.preferenceTagIds"
    if len(tag_ids) >= 2:
        assert "각각 최소 1개" in response.json()["fields"][0]["message"]
    assert "set-cookie" not in response.headers
    assert flow.client.cookies.get(settings.session_cookie_name) == token
    assert_no_onboarding_writes(flow)


def test_any_active_chip_and_an_untagged_work_can_be_saved(v2_flow):
    flow = v2_flow
    csrf, _ = pending_onboarding(flow)
    # 13 has no chips and 105 is linked to no chosen work: both are valid choices.
    response = signup_request(flow, csrf, v2_answers(favoriteEntityIds=[13], preferenceTagIds=[105, 103]))
    assert response.status_code == 201, response.text
    with Session(flow.engine) as session:
        assert set(session.scalars(select(TapPreferenceTag.tag_id))) == {105, 103}


@pytest.mark.parametrize("changes,field", [
    ({"favoriteEntityIds": [999]}, "answers.favoriteEntityIds"),
    ({"favoriteEntityIds": [14]}, "answers.favoriteEntityIds"),
    ({"favoriteEntityIds": [21]}, "answers.favoriteEntityIds"),
    ({"preferenceTagIds": [101, 999]}, "answers.preferenceTagIds"),
    ({"preferenceTagIds": [101, 104]}, "answers.preferenceTagIds"),
    ({"preferenceTagIds": [103, 201]}, "answers.preferenceTagIds"),
    ({"favoriteEntityIds": []}, "answers.favoriteEntityIds"),
    ({"preferenceTagIds": [101, 101]}, "answers.preferenceTagIds"),
])
def test_v2_invalid_selection_never_writes_partial_onboarding(v2_flow, changes, field):
    flow = v2_flow
    csrf, token = pending_onboarding(flow)
    response = signup_request(flow, csrf, v2_answers(**changes))
    assert response.status_code == 422, response.text
    assert response.json()["fields"][0]["field"] == field
    assert "set-cookie" not in response.headers
    assert flow.client.cookies.get(settings.session_cookie_name) == token
    assert_no_onboarding_writes(flow)


@pytest.mark.parametrize("catalogue_change,field", [
    ("UPDATE entity SET is_active = 0 WHERE entity_id = 11", "answers.favoriteEntityIds"),
    ("UPDATE preference_tag SET is_active = 0 WHERE tag_id = 103", "answers.preferenceTagIds"),
    ("UPDATE preference_tag SET kind = 'genre' WHERE tag_id = 103", "answers.preferenceTagIds"),
])
def test_completion_revalidates_catalogue_after_candidate_preview(v2_flow, catalogue_change, field):
    flow = v2_flow
    csrf, _ = pending_onboarding(flow)
    assert candidates(flow, csrf).status_code == 200
    with flow.engine.begin() as connection:
        connection.exec_driver_sql(catalogue_change)
    response = signup_request(flow, csrf, v2_answers())
    assert response.status_code == 422 and response.json()["fields"][0]["field"] == field
    assert_no_onboarding_writes(flow)


def test_work_tag_hint_removal_does_not_restrict_topic_chip_selection(v2_flow):
    flow = v2_flow
    csrf, _ = pending_onboarding(flow)
    assert candidates(flow, csrf).status_code == 200
    with flow.engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM entity_tag WHERE entity_id = 11 AND tag_id = 103")
    response = signup_request(flow, csrf, v2_answers())
    assert response.status_code == 201, response.text
    assert response.json()["answers"]["preferenceTagIds"] == [101, 103]


@pytest.mark.parametrize("failure_stage", ["answers_insert", "enabled"])
def test_v2_storage_failure_rolls_back_profile_choices_and_consents(v2_flow, failure_stage):
    flow = v2_flow
    csrf, token = pending_onboarding(flow)
    setattr(flow.failure, failure_stage, True)
    response = signup_request(flow, csrf, v2_answers())
    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    assert flow.client.cookies.get(settings.session_cookie_name) == token
    assert_no_onboarding_writes(flow)


def test_birthdate_contract_remains_rejected_for_v2(v2_flow):
    flow = v2_flow
    csrf, _ = pending_onboarding(flow)
    response = signup_request(flow, csrf, v2_answers(), birthDate="2000-01-01")
    assert response.status_code == 422 and response.json()["fields"][0]["field"] == "birthDate"
    assert_no_onboarding_writes(flow)

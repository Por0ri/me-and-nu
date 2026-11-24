"""My Page taste edit (temporary): /me/taste read, search, chips and save on the disposable SQLite flow."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Integer, MetaData, select, update
from sqlalchemy.orm import Session

from app.models import OnboardingAnswersRecord, RecommendationRound, Tap, TapEntity, TapPreferenceTag, Topic, UserAccount
from test_oauth_postgres_flow import onboarding_flow, pending_onboarding, postgres_flow, signup_request  # noqa: F401
from test_onboarding_v2 import ranking_rows, v2_answers, v2_flow  # noqa: F401

KST = timezone(timedelta(hours=9))


@pytest.fixture
def taste_flow(v2_flow):  # noqa: F811
    flow = v2_flow
    metadata = MetaData()
    for model in (UserAccount, Topic, Tap, RecommendationRound):
        table = model.__table__.to_metadata(metadata)
        for column in table.primary_key.columns:
            if len(table.primary_key.columns) == 1 and isinstance(column.type, Integer):
                column.type = Integer()
    metadata.create_all(flow.engine)
    return flow


def signed_up(flow):
    csrf, _ = pending_onboarding(flow)
    assert signup_request(flow, csrf, v2_answers()).status_code == 201
    return flow.client.get("/api/v1/auth/session").json()["csrfToken"]


def put_taste(flow, csrf, answers, topic_id=1):
    return flow.client.put("/api/v1/me/taste", json={"topicId": topic_id, "answers": answers},
                           headers={"X-CSRF-Token": csrf})


def saved(flow):
    with Session(flow.engine) as session:
        return (set(session.scalars(select(TapEntity.entity_id))), set(session.scalars(select(TapPreferenceTag.tag_id))),
                session.scalar(select(OnboardingAnswersRecord.consumption_type)))


def test_taste_needs_an_onboarded_session(taste_flow):
    flow = taste_flow
    assert flow.client.get("/api/v1/me/taste?topicId=1").status_code == 401
    csrf, _ = pending_onboarding(flow)
    assert flow.client.get("/api/v1/me/taste?topicId=1").status_code == 403
    assert flow.client.get("/api/v1/me/taste/works?topicId=1&q=hero").status_code == 403
    assert put_taste(flow, csrf, v2_answers()).status_code == 403


def test_taste_returns_the_onboarding_answers(taste_flow):
    flow = taste_flow
    signed_up(flow)
    body = flow.client.get("/api/v1/me/taste?topicId=1").json()
    assert body["topicId"] == 1 and [work["entityId"] for work in body["works"]] == [11, 12]
    assert body["works"][0]["name"] == "Hero Academy" and body["works"][0]["selectable"] is True
    assert body["preferenceTagIds"] == [101, 103] and body["consumptionType"] == "world"
    assert body["consumptionTypes"] == ["world"]
    assert flow.client.get("/api/v1/me/taste?topicId=2").status_code == 404      # no tap in that Topic


def test_onboarded_users_can_search_works_and_list_chips(taste_flow):
    flow = taste_flow
    csrf = signed_up(flow)
    works = flow.client.get("/api/v1/me/taste/works?topicId=1&q=hero")
    assert works.status_code == 200 and 11 in [work["entityId"] for work in works.json()["items"]]
    body = {"topicId": 1, "entityIds": [13]}
    assert flow.client.post("/api/v1/me/taste/preference-options", json=body,
                            headers={"X-CSRF-Token": "invalid"}).status_code == 403
    options = flow.client.post("/api/v1/me/taste/preference-options", json=body, headers={"X-CSRF-Token": csrf})
    assert options.status_code == 200 and {tag["tagId"] for tag in options.json()["genres"]} >= {101, 102}
    assert saved(flow) == ({11, 12}, {101, 103}, "world")


def test_saving_replaces_the_answers_and_drops_the_recent_rounds(taste_flow):
    flow = taste_flow
    csrf = signed_up(flow)
    today = datetime.now(KST).date()
    with Session(flow.engine) as session:
        tap_id = session.scalar(select(Tap.tap_id))
        session.add_all([RecommendationRound(tap_id=tap_id, service_date=day, algorithm_version="v1")
                         for day in (today, today - timedelta(days=1), today - timedelta(days=3))])
        session.commit()

    response = put_taste(flow, csrf, v2_answers(favoriteEntityIds=[15, 13], preferenceTagIds=[102, 103],
                                                consumptionTypes=["news", "ost"]))
    assert response.status_code == 200, response.text
    assert [work["entityId"] for work in response.json()["works"]] == [13, 15]
    assert response.json()["preferenceTagIds"] == [102, 103] and response.json()["consumptionType"] == "news"
    assert response.json()["consumptionTypes"] == ["news", "ost"]
    assert saved(flow) == ({13, 15}, {102, 103}, "news") and ranking_rows(flow) == [(1, "news"), (2, "ost")]
    with Session(flow.engine) as session:
        record = session.scalar(select(OnboardingAnswersRecord))
        assert (record.schema_version, record.q1_choice, record.q2_choice) == (2, None, None)
        # Today/yesterday rounds go so the keep-on batch rebuilds today's round; older history stays.
        assert list(session.scalars(select(RecommendationRound.service_date))) == [today - timedelta(days=3)]
    assert flow.client.get("/api/v1/auth/session").json()["sessionState"] == "active"


@pytest.mark.parametrize("changes", [
    {"preferenceTagIds": [101, 102]},           # no theme chip
    {"favoriteEntityIds": [14]},                # inactive work
    {"preferenceTagIds": [101, 104]},           # inactive chip
])
def test_an_invalid_save_changes_nothing(taste_flow, changes):
    flow = taste_flow
    csrf = signed_up(flow)
    assert put_taste(flow, "invalid", v2_answers(favoriteEntityIds=[13])).status_code == 403
    assert put_taste(flow, csrf, v2_answers(**changes)).status_code == 422
    assert saved(flow) == ({11, 12}, {101, 103}, "world") and ranking_rows(flow) == [(1, "world")]


def test_a_new_ranking_replaces_the_old_one_and_the_older_screen_saves_one(taste_flow):
    flow = taste_flow
    csrf = signed_up(flow)
    assert put_taste(flow, csrf, v2_answers(consumptionTypes=["ost", "news", "world"])).status_code == 200
    assert ranking_rows(flow) == [(1, "ost"), (2, "news"), (3, "world")]
    older = {**{k: v for k, v in v2_answers().items() if k != "consumptionTypes"}, "consumptionType": "goods"}
    response = put_taste(flow, csrf, older)
    assert response.status_code == 200, response.text
    assert (response.json()["consumptionType"], response.json()["consumptionTypes"]) == ("goods", ["goods"])
    assert ranking_rows(flow) == [(1, "goods")]


def test_taste_reads_rank_1_alone_after_the_production_code_changed_only_it(taste_flow):
    # The production main code shares the database and rewrites only onboarding_answers.consumption_type.
    flow = taste_flow
    csrf = signed_up(flow)
    assert put_taste(flow, csrf, v2_answers(consumptionTypes=["ost", "news"])).status_code == 200
    assert flow.client.get("/api/v1/me/taste?topicId=1").json()["consumptionTypes"] == ["ost", "news"]
    with Session(flow.engine) as session:
        session.execute(update(OnboardingAnswersRecord).values(consumption_type="world"))
        session.commit()
    body = flow.client.get("/api/v1/me/taste?topicId=1").json()
    assert (body["consumptionType"], body["consumptionTypes"]) == ("world", ["world"])


def test_an_old_v1_member_reads_no_ranking(taste_flow):
    # v1 answers {q1, q2} are still accepted; they never had a Q3 ranking.
    flow = taste_flow
    csrf, _ = pending_onboarding(flow)
    assert signup_request(flow, csrf, {"q1": "ost", "q2": "events"}).status_code == 201
    body = flow.client.get("/api/v1/me/taste?topicId=1").json()
    assert (body["consumptionType"], body["consumptionTypes"]) == (None, [])
    assert ranking_rows(flow) == []

from ani_menu_agent.nodes import route_review
from ani_menu_agent.policy import POLICY


def test_continuous_review_loop_archives_after_third_rejection():
    assert POLICY.max_review_failures == 3
    state = {
        "editorial_decision": "revise",
        "review_failures": 3,
        "max_revision": POLICY.max_review_failures,
        "usage_records": [],
    }
    assert route_review(state) == "archive_rejected"


def test_continuous_review_loop_returns_first_two_rejections_for_revision():
    state = {
        "editorial_decision": "revise",
        "review_failures": 2,
        "max_revision": POLICY.max_review_failures,
        "usage_records": [],
    }
    assert route_review(state) == "revision_checkpoint"

from ani_menu_agent.agents.content_generation.detail_writer import (
    DETAIL_WRITER_PROMPT,
    _detail_writer_revision_issues,
)
from ani_menu_agent.agents.reviewer.prompt import REVIEWER_PROMPT
from ani_menu_agent.models import QualityIssue
from ani_menu_agent.prompts import COPY_WRITER


def test_copy_writer_preserves_meaning_in_puns_and_avoids_ambiguous_compression() -> None:
    assert "Translate the meaning and reading experience, not just the sounds" in COPY_WRITER
    assert "Weak" in COPY_WRITER
    assert "로만의 카메라 뒤" in COPY_WRITER
    assert "subject, action, object, and referent explicit" in COPY_WRITER
    assert "Treat term_glossary and verified-fact entity names as locked references" in COPY_WRITER
    assert "Track who did what in every sentence" in COPY_WRITER
    assert (
        "Do not translate English idioms, Japanese phrasing, or review headlines word-for-word"
        in COPY_WRITER
    )


def test_detail_writer_distinguishes_attributed_review_opinions_from_facts() -> None:
    assert "Attribute review judgments to the reviewer" in DETAIL_WRITER_PROMPT
    assert "unsupported praise" in DETAIL_WRITER_PROMPT
    assert "clear, polished Korean fan-editorial prose" in DETAIL_WRITER_PROMPT


def test_detail_writer_revision_feedback_accepts_serialized_and_model_issues() -> None:
    issues = _detail_writer_revision_issues(
        [
            QualityIssue(
                code="DETAIL_BODY_FACTUAL_SUPPORT",
                message="근거 보완",
                owner="detail_writer",
            ),
            {"code": "COPY_FLOW", "message": "카피 수정", "owner": "copy_writer"},
            {"code": "DETAIL_BODY_CLARITY", "message": "문장 명확화", "owner": "detail_writer"},
        ]
    )
    assert [issue["code"] for issue in issues] == [
        "DETAIL_BODY_FACTUAL_SUPPORT",
        "DETAIL_BODY_CLARITY",
    ]


def test_reviewer_gives_specific_repairs_and_calibrates_opinion_support() -> None:
    assert "give one concise" in REVIEWER_PROMPT
    assert "Weak" in REVIEWER_PROMPT
    assert (
        "Treat a reviewer's explicitly attributed opinion as a sourced opinion" in REVIEWER_PROMPT
    )
    assert "not forced slang" in REVIEWER_PROMPT

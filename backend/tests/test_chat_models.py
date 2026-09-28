from sqlalchemy import ForeignKeyConstraint

from app.db.base import Base
from app.models import (
    ChatFactCache,
    ChatMessage,
    ChatSession,
    ChatUnansweredQuestion,
    LongTermMemory,
    UserOperation,
)


def test_chat_models_are_registered():
    models = (
        ChatSession,
        ChatMessage,
        LongTermMemory,
        UserOperation,
        ChatFactCache,
        ChatUnansweredQuestion,
    )
    assert all(model.__table__.metadata is Base.metadata for model in models)


def test_chat_session_requires_topic_tab_owned_by_same_user():
    assert ChatSession.__table__.c.tap_id.nullable is False
    assert any(
        isinstance(constraint, ForeignKeyConstraint)
        and {column.name for column in constraint.columns} == {"tap_id", "user_id"}
        and constraint.ondelete == "CASCADE"
        for constraint in ChatSession.__table__.constraints
    )


def test_chat_message_keeps_erd_columns_and_api_fields():
    columns = set(ChatMessage.__table__.c.keys())
    assert {
        "chat_session_id",
        "sender_role",
        "message_body",
        "token_count",
        "written_at",
        "answer_sources",
    } <= columns
    assert {
        "client_message_id",
        "selected_text",
        "agent_run_id",
        "result_type",
        "notices",
    } <= columns


def test_deleting_a_session_removes_its_messages_and_unanswered_records():
    session_fk = next(iter(ChatMessage.__table__.c.chat_session_id.foreign_keys))
    message_fk = next(
        iter(ChatUnansweredQuestion.__table__.c.chat_message_id.foreign_keys)
    )
    assert session_fk.ondelete == "CASCADE"
    assert message_fk.ondelete == "CASCADE"

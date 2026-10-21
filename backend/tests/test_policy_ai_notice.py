from unittest.mock import AsyncMock

import pytest

from app.schemas.chat import AI_NOTICE, ChatSessionResponse
from app.main import create_app
from app.services import policy_service


@pytest.mark.asyncio
async def test_policy_and_session_disclose_ai_generation_with_source_guidance(monkeypatch):
    monkeypatch.setattr(policy_service, "get_current_policies", AsyncMock(return_value={}))

    policies = await policy_service.get_policies(object())
    session = ChatSessionResponse(session_id=1, topic_id=1)

    assert policies.ai_notice == session.ai_notice == AI_NOTICE
    assert "AI가 생성한 답변" in policies.ai_notice
    assert "출처와 근거를 확인" in policies.ai_notice
    assert policies.chat_retention.days is None
    assert policies.chat_retention.status == "undecided"
    schema = create_app(enable_dev_api=False).openapi()
    example = schema["paths"]["/api/v1/policies"]["get"]["responses"]["200"]["content"]["application/json"]["example"]
    assert example["aiNotice"] == policies.ai_notice

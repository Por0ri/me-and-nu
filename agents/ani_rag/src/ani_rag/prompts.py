"""Compatibility exports for role-owned generation and review prompts."""

from ani_rag.agents.content_generation.prompts import (
    COPY_WRITER_PROMPT as GENERATION_SYSTEM_PROMPT,
    DETAIL_WRITER_PROMPT,
    LAYOUT_POLICY,
)
from ani_rag.agents.reviewer.prompts import REVIEWER_PROMPT as REVIEW_SYSTEM_PROMPT

__all__ = [
    "GENERATION_SYSTEM_PROMPT",
    "DETAIL_WRITER_PROMPT",
    "LAYOUT_POLICY",
    "REVIEW_SYSTEM_PROMPT",
]

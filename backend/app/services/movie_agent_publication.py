"""Publish a persisted movie Agent draft into the public V1 content feed."""

from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, AgentRunSource, Content, ContentSource, ContentTag, Draft, Subtopic


PREVIEW_MARKER = "[개발용 미리보기 · 검토 필요]"


class MovieAgentPublicationError(Exception):
    """A persisted run cannot safely be made public."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _valid_source_url(raw_url: object) -> str | None:
    if not isinstance(raw_url, str):
        return None
    url = raw_url.strip()
    if not url or len(url) > 1000 or any(char.isspace() for char in url):
        return None
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        _ = parsed.port  # Reject malformed port numbers and values above 65535.
    except ValueError:
        return None
    return url


def _summary(body: str, *, preview: bool) -> str:
    compact = " ".join(body.split())
    excerpt = compact[:180].rstrip()
    if len(compact) > 180:
        excerpt += "…"
    return f"{PREVIEW_MARKER} {excerpt}" if preview else excerpt


def _check_existing_publication(content: Content, *, force_preview: bool) -> None:
    basis = content.ai_judgment_basis if isinstance(content.ai_judgment_basis, dict) else {}
    if basis.get("preview") is True and not force_preview:
        raise MovieAgentPublicationError(
            "AGENT_DRAFT_PREVIEW_ONLY",
            "기존 게시물이 검토 필요 미리보기입니다. 승인된 일반 콘텐츠로 발행할 수 없습니다.",
        )


async def publish_movie_agent_run(
    db: AsyncSession,
    agent_run_id: int,
    *,
    force_preview: bool = False,
) -> Content:
    """Publish once per draft, committing content, sources, and tag atomically.

    The caller must expose ``force_preview`` only from a local development route.
    Locking the run serializes concurrent calls through this service; the unique
    ``content.source_draft_id`` index is a second line of duplicate protection.
    """
    draft_id: int | None = None
    try:
        run = await db.scalar(
            select(AgentRun)
            .where(AgentRun.agent_run_id == agent_run_id, AgentRun.agent_code == "movie_review")
            .with_for_update()
        )
        if run is None:
            raise MovieAgentPublicationError(
                "AGENT_RUN_NOT_FOUND", "영화 Agent 실행 기록을 찾을 수 없습니다."
            )

        draft = await db.scalar(select(Draft).where(Draft.agent_run_id == agent_run_id))
        if draft is None:
            raise MovieAgentPublicationError(
                "AGENT_DRAFT_NOT_FOUND", "발행할 영화 Agent 초안이 없습니다."
            )
        draft_id = draft.draft_id

        existing = await db.scalar(
            select(Content).where(Content.source_draft_id == draft_id)
        )
        if existing is not None:
            _check_existing_publication(existing, force_preview=force_preview)
            await db.commit()
            return existing

        if run.status != "succeeded" or draft.topic_id != run.topic_id:
            raise MovieAgentPublicationError(
                "AGENT_RUN_NOT_PUBLISHABLE", "완료된 영화 Agent 실행과 일치하는 초안만 발행할 수 있습니다."
            )

        candidate = run.outcome == "publish_candidate" and draft.status == "approved"
        rejected = run.outcome == "rejected_archive" and draft.status == "discarded"
        if not candidate and not (force_preview and rejected):
            raise MovieAgentPublicationError(
                "AGENT_DRAFT_NOT_PUBLISHABLE", "승인된 초안만 발행할 수 있습니다. 탈락 초안은 개발용 미리보기에서만 허용됩니다."
            )

        factcheck = draft.factcheck_result if isinstance(draft.factcheck_result, dict) else {}
        requires_review = factcheck.get("requires_review") is not False
        if requires_review and not force_preview:
            raise MovieAgentPublicationError(
                "AGENT_DRAFT_REVIEW_REQUIRED", "사실 확인이 필요한 초안은 일반 발행할 수 없습니다."
            )

        title = draft.title.strip() if isinstance(draft.title, str) else ""
        body = draft.body.strip() if isinstance(draft.body, str) else ""
        if not title or len(title) > 300 or not body:
            raise MovieAgentPublicationError(
                "AGENT_DRAFT_INVALID", "제목과 본문이 있는 유효한 초안만 발행할 수 있습니다."
            )

        source_rows = (
            await db.scalars(
                select(AgentRunSource)
                .where(AgentRunSource.agent_run_id == agent_run_id)
                .order_by(AgentRunSource.agent_run_source_id)
            )
        ).all()
        sources: list[tuple[AgentRunSource, str]] = []
        seen_urls: set[str] = set()
        for row in source_rows:
            url = _valid_source_url(row.source_url)
            if url is None or url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append((row, url))
        if not sources:
            raise MovieAgentPublicationError(
                "AGENT_SOURCE_REQUIRED", "유효한 출처 URL이 최소 하나 필요합니다."
            )

        subtopic = await db.scalar(
            select(Subtopic)
            .where(
                Subtopic.topic_id == draft.topic_id,
                Subtopic.subtopic_name == "영화 리뷰",
            )
            .order_by(Subtopic.subtopic_id)
            .limit(1)
        )
        if subtopic is None:
            raise MovieAgentPublicationError(
                "MOVIE_SUBTOPIC_NOT_FOUND", "영화 리뷰 세부 토픽이 없습니다. 영화 기준 데이터를 먼저 준비하세요."
            )

        preview = bool(force_preview)
        content = Content(
            source_draft_id=draft_id,
            topic_id=draft.topic_id,
            production_type="ai",
            content_type="movie_review",
            title=title,
            body=body,
            summary=_summary(body, preview=preview),
            published_at=datetime.now(timezone.utc),
            status="active",
            judgment_status="needs_review" if preview or requires_review else "confirmed",
            ai_judgment_basis={
                "source": "movie_agent",
                "agent_run_id": agent_run_id,
                "draft_id": draft_id,
                "outcome": run.outcome,
                "preview": preview,
                "requires_review": requires_review,
            },
        )
        db.add(content)
        await db.flush()

        for citation_order, (source, url) in enumerate(sources, start=1):
            db.add(
                ContentSource(
                    content_id=content.content_id,
                    agent_run_source_id=source.agent_run_source_id,
                    source_site_id=source.source_site_id,
                    source_url=url,
                    source_title=source.source_title,
                    source_role="primary" if citation_order == 1 else "citation",
                    citation_order=citation_order,
                )
            )
        db.add(
            ContentTag(
                content_id=content.content_id,
                topic_id=draft.topic_id,
                subtopic_id=subtopic.subtopic_id,
            )
        )
        if not preview:
            draft.status = "published"
        await db.commit()
        await db.refresh(content)
        return content
    except IntegrityError:
        await db.rollback()
        # A concurrent publisher that did not use the run lock may have won the
        # unique source_draft_id constraint. Return that result if it exists.
        if draft_id is not None:
            existing = await db.scalar(
                select(Content).where(Content.source_draft_id == draft_id)
            )
            if existing is not None:
                _check_existing_publication(existing, force_preview=force_preview)
                await db.commit()
                return existing
        raise
    except Exception:
        await db.rollback()
        raise

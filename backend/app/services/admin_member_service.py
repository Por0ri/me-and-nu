"""Single-snapshot, bounded member reads with deliberate PII projection.

Search is supplied separately from URL query fields. Only the email needed for
masking crosses the DB boundary; auth identifiers, password/session data,
withdrawal reasons and uncollected demographic fields are never selected.
"""

import re

from sqlalchemy import JSON, and_, case, distinct, func, or_, select, true
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import (
    Content, ContentReaction, Entity, OnboardingAnswersRecord, PreferenceTag,
    SavedItem, Tap, TapEntity, TapPreferenceTag, Topic, UserAccount,
)
from app.schemas.admin_members import (
    AdminMemberDetail, AdminMemberListResponse, AdminMemberOnboarding,
    AdminMemberQuery, AdminMemberSummary,
)

CONSUMPTION_LABELS = {
    "world": "설정과 세계관을 파고든다", "relationships": "캐릭터의 관계성과 감정선에 빠진다",
    "ost": "관련 OST를 찾아듣는다", "goods": "굿즈를 모으거나 구매 정보를 찾는다",
    "events": "팝업 · 전시 · 이벤트 등을 찾아간다", "news": "새로운 소식을 빠르게 확인한다",
}


def mask_email(value: str | None) -> str | None:
    """Show at most two local-part characters, concealing a one-letter name too."""
    if not isinstance(value, str) or len(value) > 254 or value.count("@") != 1:
        return None
    local, domain = value.split("@")
    if (not local or len(local) > 64 or not re.fullmatch(r"[^\s@.]+(?:\.[^\s@.]+)+", domain)
            or any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value)):
        return None
    prefix = local[:min(2, max(0, len(local) - 1))] or "*"
    return prefix + "***@" + domain


def _withdrawn():
    return or_(UserAccount.withdrawn_at.is_not(None), UserAccount.account_status == "withdrawn")


def _active_taps():
    return and_(Tap.user_id == UserAccount.user_id, Tap.deleted_at.is_(None), Topic.is_active.is_(True))


def _domains(member_id=None):
    owner = UserAccount.user_id if member_id is None else member_id
    return (select(func.json_agg(Topic.topic_name, type_=JSON)).select_from(Tap)
            .join(Topic, Topic.topic_id == Tap.topic_id)
            .where(Tap.user_id == owner, Tap.deleted_at.is_(None), Topic.is_active.is_(True))
            .correlate_except(Tap, Topic).scalar_subquery())


def _summary_columns(*, include_domains=True):
    withdrawn = _withdrawn()
    columns = (
        UserAccount.user_id.label("member_id"),
        case((withdrawn, "탈퇴 회원"), else_=UserAccount.nickname).label("nickname"),
        case((withdrawn, None), else_=UserAccount.email).label("email"),
        case((withdrawn, None), else_=UserAccount.auth_provider).label("provider"),
        case((withdrawn, None), else_=UserAccount.joined_at).label("joined_at"),
        case((withdrawn, None), else_=UserAccount.last_login_at).label("last_login_at"),
        case((withdrawn, "withdrawn"), else_=UserAccount.account_status).label("status"),
        # Ordering remains deterministic for redacted members without exposing
        # their registration timestamps in the returned projection.
        UserAccount.joined_at.label("sort_joined_at"),
    )
    return (*columns, case((withdrawn, None), else_=_domains()).label("domains")) if include_domains else columns


def _labels(values) -> list[str]:
    return sorted({value for value in (values or ()) if isinstance(value, str) and value})


def _summary(row) -> AdminMemberSummary:
    return AdminMemberSummary(
        id=str(row["member_id"]), nickname=row["nickname"], masked_email=mask_email(row["email"]),
        provider=row["provider"], domain=" · ".join(_labels(row["domains"])) or None,
        joined_at=row["joined_at"], last_login_at=row["last_login_at"], status=row["status"],
    )


def member_list_statement(query: AdminMemberQuery, search: str = ""):
    rows = select(*_summary_columns(include_domains=False)).subquery("member_projection")
    filtered = select(rows)
    if query.status:
        filtered = filtered.where(rows.c.status == query.status)
    if query.provider:
        filtered = filtered.where(rows.c.provider == query.provider)
    if search:
        # Search wildcard characters literally. Bound parameters prevent SQL
        # injection; redacted expressions prevent withdrawn-identity probing.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + escaped + "%"
        filtered = filtered.where(or_(rows.c.nickname.ilike(pattern, escape="\\"), rows.c.email.ilike(pattern, escape="\\")))
    filtered = filtered.cte("filtered_members")
    descending = query.sort == "recent"
    ordered = lambda c: (c.sort_joined_at.desc(), c.member_id.desc()) if descending else (c.sort_joined_at.asc(), c.member_id.asc())
    page = (select(filtered).order_by(*ordered(filtered.c))
            .offset((query.page - 1) * query.page_size).limit(query.page_size).cte("member_page"))
    total = select(func.count().label("total")).select_from(filtered).cte("member_total")
    # Aggregate topics only for the selected page, not every matching account.
    domains = case((page.c.status == "withdrawn", None), else_=_domains(page.c.member_id)).label("domains")
    return select(total.c.total, *page.c, domains).select_from(total.outerjoin(page, true())).order_by(*ordered(page.c))


async def _read(db: AsyncSession, statement):
    try:
        return (await db.execute(statement)).mappings().all()
    except SQLAlchemyError:
        # DB exceptions may contain search parameters. Never let them enter
        # general exception logs or public response bodies.
        raise ApiError(503, "ADMIN_MEMBERS_UNAVAILABLE", "회원 정보를 조회할 수 없습니다.") from None


async def list_members(db: AsyncSession, query: AdminMemberQuery, search: str = "") -> AdminMemberListResponse:
    rows = await _read(db, member_list_statement(query, search))
    return AdminMemberListResponse(items=[_summary(row) for row in rows if row["member_id"] is not None],
                                   page=query.page, page_size=query.page_size, total=rows[0]["total"] if rows else 0)


def _engagement_count(model, active):
    # Count distinct content, not rows or taps. resurface_off remains saved;
    # deleted saves, deleted taps, hidden/deleted content do not contribute.
    return (select(func.count(distinct(model.content_id))).select_from(model)
            .join(Tap, and_(Tap.tap_id == model.tap_id, Tap.user_id == model.user_id))
            .join(Topic, Topic.topic_id == Tap.topic_id)
            .join(Content, Content.content_id == model.content_id)
            .where(model.user_id == UserAccount.user_id, _active_taps(), active,
                   Content.deleted_at.is_(None), Content.status == "active", Content.topic_id == Tap.topic_id)
            .correlate(UserAccount).scalar_subquery())


def _onboarding_values(column, selection=None, *, kind=None):
    statement = (select(func.json_agg(column, type_=JSON)).select_from(Tap)
                 .join(Topic, Topic.topic_id == Tap.topic_id)
                 .join(OnboardingAnswersRecord, OnboardingAnswersRecord.tap_id == Tap.tap_id))
    if selection == "work":
        statement = (statement.join(TapEntity, and_(TapEntity.tap_id == Tap.tap_id, TapEntity.topic_id == Tap.topic_id))
                     .join(Entity, and_(Entity.entity_id == TapEntity.entity_id, Entity.topic_id == Tap.topic_id)))
    elif selection == "tag":
        statement = (statement.join(TapPreferenceTag, and_(TapPreferenceTag.tap_id == Tap.tap_id, TapPreferenceTag.topic_id == Tap.topic_id))
                     .join(PreferenceTag, and_(PreferenceTag.tag_id == TapPreferenceTag.tag_id, PreferenceTag.topic_id == Tap.topic_id))
                     .where(PreferenceTag.kind == kind))
    return statement.where(_active_taps(), OnboardingAnswersRecord.schema_version == 2).correlate(UserAccount).scalar_subquery()


def member_detail_statement(member_id: int):
    withdrawn = _withdrawn()
    redacted = lambda expression, name: case((withdrawn, None), else_=expression).label(name)
    return select(
        *_summary_columns(),
        redacted(_engagement_count(SavedItem, SavedItem.status.in_(("kept", "resurface_off"))), "saved_count"),
        redacted(_engagement_count(ContentReaction, ContentReaction.is_liked.is_(True)), "liked_count"),
        redacted(_onboarding_values(Entity.name, "work"), "works"),
        redacted(_onboarding_values(PreferenceTag.display_name, "tag", kind="genre"), "genres"),
        redacted(_onboarding_values(PreferenceTag.display_name, "tag", kind="theme"), "themes"),
        redacted(_onboarding_values(OnboardingAnswersRecord.consumption_type), "consumption_types"),
    ).where(UserAccount.user_id == member_id)


async def get_member(db: AsyncSession, member_id: int) -> AdminMemberDetail:
    rows = await _read(db, member_detail_statement(member_id))
    if not rows:
        raise ApiError(404, "MEMBER_NOT_FOUND", "회원을 찾을 수 없습니다.")
    row = rows[0]
    consumption = _labels(row["consumption_types"])
    onboarding = AdminMemberOnboarding(
        works=_labels(row["works"]), genres=_labels(row["genres"]), themes=_labels(row["themes"]),
        content_types=[CONSUMPTION_LABELS[code] for code in consumption if code in CONSUMPTION_LABELS],
    ) if consumption else None
    return AdminMemberDetail(**_summary(row).model_dump(), onboarding=onboarding,
                             saved_count=row["saved_count"], liked_count=row["liked_count"])

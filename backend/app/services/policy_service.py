from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import ConsentHistory, Policy
from app.repositories.policy_repository import add_consent_history, get_current_policies, get_latest_consents
from app.schemas.policy import (
    ChatRetention,
    ConsentHistoryItem,
    ConsentHistoryResponse,
    ConsentInput,
    ConsentItem,
    ConsentPatchItem,
    ConsentPatchResponse,
    PoliciesResponse,
)


def validate_consent_inputs(
    items: list[ConsentInput],
    current: dict[str, Policy],
    *,
    require_all: bool,
) -> dict[str, ConsentInput]:
    submitted: dict[str, ConsentInput] = {}
    for item in items:
        if item.type in submitted or item.type not in current:
            raise ApiError(422, "VALIDATION_ERROR", "동의 항목이 중복되었거나 존재하지 않습니다.")
        submitted[item.type] = item
    if require_all and submitted.keys() != current.keys():
        raise ApiError(422, "REQUIRED_CONSENT_MISSING", "표시된 모든 동의 항목을 제출해 주세요.")
    for consent_type, item in submitted.items():
        policy = current[consent_type]
        if item.policy_version != policy.policy_version:
            raise ApiError(409, "POLICY_VERSION_CONFLICT", "정책 버전이 변경되었습니다. 정책을 다시 조회해 주세요.")
        if policy.is_required and not item.agreed:
            raise ApiError(422, "REQUIRED_CONSENT_MISSING", "필수 정책에 동의해 주세요.")
    return submitted


async def get_policies(db: AsyncSession) -> PoliciesResponse:
    policies = await get_current_policies(db)
    return PoliciesResponse(
        consent_items=[
            ConsentItem(
                type=policy.policy_type,
                policy_version=policy.policy_version,
                required=policy.is_required,
                text=policy.content or policy.content_url or "",
            )
            for policy in sorted(policies.values(), key=lambda value: value.policy_id)
        ],
        ai_notice="AI 기능은 현재 로컬 V1 테스트 범위에 포함되지 않습니다.",
        chat_retention=ChatRetention(),
    )


def _history_item(row: ConsentHistory) -> ConsentHistoryItem:
    return ConsentHistoryItem(
        type=row.consent_type,
        policy_version=row.policy_version,
        agreed=row.is_agreed,
        agreed_at=row.agreed_at,
        withdrawn_at=row.revoked_at,
    )


async def get_user_consents(db: AsyncSession, user_id: int) -> ConsentHistoryResponse:
    latest = await get_latest_consents(db, user_id)
    return ConsentHistoryResponse(items=[_history_item(latest[key]) for key in sorted(latest)])


async def patch_user_consents(
    db: AsyncSession,
    *,
    user_id: int,
    items: list[ConsentInput],
) -> ConsentPatchResponse:
    try:
        current = await get_current_policies(db)
        submitted = validate_consent_inputs(items, current, require_all=False)
        latest = await get_latest_consents(db, user_id)
        updated: list[ConsentPatchItem] = []
        for consent_type, item in submitted.items():
            prior = latest.get(consent_type)
            if prior is not None and prior.is_agreed == item.agreed and prior.policy_version == item.policy_version:
                row = prior
            else:
                row = add_consent_history(
                    db,
                    user_id=user_id,
                    consent_type=consent_type,
                    policy_version=item.policy_version,
                    agreed=item.agreed,
                    was_agreed=prior.is_agreed if prior is not None else False,
                )
            updated.append(ConsentPatchItem(type=consent_type, agreed=item.agreed, updated_at=row.created_at))
        await db.flush()
        await db.commit()
        return ConsentPatchResponse(items=updated)
    except Exception:
        await db.rollback()
        raise

"""회원 탈퇴(API-009)와 유예 중 복구(API-010) 응답입니다."""

from datetime import datetime
from typing import Literal

from app.schemas.auth import SessionUser
from app.schemas.common import CamelModel


class WithdrawalResponse(CamelModel):
    status: Literal["withdrawal_pending"]
    mode: Literal["grace"]
    scheduled_delete_at: datetime


class WithdrawalRestorationResponse(CamelModel):
    user: SessionUser
    status: Literal["active"]
    onboarding_completed: bool

"""회원 탈퇴(API-009) 응답입니다."""

from datetime import datetime
from typing import Literal

from app.schemas.common import CamelModel


class WithdrawalResponse(CamelModel):
    # 명세의 응답 모양을 따르되, 지금은 즉시 삭제만 하므로 상태는 처리 완료(withdrawn)이고
    # scheduledDeleteAt 은 실제로 지운 시각입니다.
    status: Literal["withdrawn"]
    mode: Literal["immediate"]
    scheduled_delete_at: datetime

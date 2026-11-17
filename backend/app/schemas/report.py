"""콘텐츠 신고(API-070)와 마이페이지 문의의 요청 · 응답입니다."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from app.schemas.common import CamelModel

DETAIL_MAX = 500
ReportReason = Literal["wrong_info", "spoiler", "rights", "inappropriate", "other"]
InquiryType = Literal["service", "bug"]


class ReportCreateRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    # 지금 신고할 수 있는 대상은 콘텐츠뿐입니다 (명세의 DM 은 서비스에 없음).
    target_type: Literal["content"]
    target_id: int = Field(gt=0)
    reason_code: ReportReason
    # 화면에 입력 칸이 생기면 쓰는 선택 내용 (예: '기타' 사유). 앞뒤 공백을 지우고 비면 저장하지 않습니다.
    detail: Annotated[str, StringConstraints(strip_whitespace=True, max_length=DETAIL_MAX)] | None = None

    @field_validator("detail")
    @classmethod
    def empty_detail_is_none(cls, value: str | None) -> str | None:
        return value or None


class ReportResponse(CamelModel):
    report_id: int
    status: Literal["received"]


class InquiryCreateRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    inquiry_type: InquiryType
    detail: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DETAIL_MAX)]


class InquiryResponse(CamelModel):
    inquiry_id: int
    status: Literal["received"]

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel


class MyReaction(CamelModel):
    liked: bool = False
    preference: Literal["positive", "negative"] | None = None


class SlideBox(CamelModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class SlideLayout(CamelModel):
    # Only presentation fields cross the public boundary, including inside boxes.
    template: str = Field(default="split", max_length=50)
    fit_mode: str = Field(default="contain_blur", max_length=50)
    background_style: Literal["blur", "palette", "gradient"] = "blur"
    image_box: SlideBox
    copy_box: SlideBox
    text_overlay_box: SlideBox = Field(
        default_factory=lambda: SlideBox(x=0.06, y=0.67, width=0.88, height=0.29)
    )
    image_scale: float = Field(default=1.0, ge=0.5, le=1.5)
    image_offset_y: float = Field(default=0.0, ge=-0.3, le=0.3)
    contrast_mode: str = Field(default="scrim_required", max_length=50)
    blur_background: bool = False


class ContentSlideResponse(CamelModel):
    image_url: str
    page: int
    role: str
    eyebrow: str
    title_lines: list[str]
    body_sentences: list[str]
    layout: SlideLayout
    width: int
    height: int


class ContentSourceResponse(CamelModel):
    url: str
    title: str | None = None
    publisher: str | None = None
    role: str
    citation_order: int | None = None


class FeedCard(CamelModel):
    kind: Literal["content"] = "content"
    id: int
    topic_id: int
    recommendation_id: int | None = None
    title: str
    production_type: Literal["human", "ai", "hybrid"]
    summary: str | None = None
    image_url: str | None = None
    slides: list[ContentSlideResponse] = Field(default_factory=list)
    source_name: str
    source_url: str
    published_at: datetime | None = None
    saved: bool = False
    saved_item_id: int | None = None
    recommendation_reason: str | None = None
    subtopic_names: list[str] = Field(default_factory=list)
    seen: bool = False
    is_promotional: bool = False
    notices: list[dict[str, str]] = Field(default_factory=list)
    my_reaction: MyReaction = Field(default_factory=MyReaction)


class FeedSection(CamelModel):
    id: str = "recommended"
    type: Literal["recommended", "subtopic"] = "recommended"
    title: str = "추천 콘텐츠"
    subtopic_id: int | None = None
    contents: list[FeedCard]


class FeedResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"selectedTopicId": 1, "sections": [], "nextCursor": None, "emptyReason": "NO_CONTENT_IN_TOPIC"}})
    selected_topic_id: int
    sections: list[FeedSection]
    next_cursor: str | None = None
    empty_reason: str | None = None


class FeedSearchResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"selectedTopicId": 1, "query": "장송의프리렌", "works": ["장송의 프리렌"], "contents": []}})
    selected_topic_id: int
    query: str
    works: list[str] = Field(default_factory=list, description="검색어에 맞는 작품의 정식 이름 (글이 없어도 채움)")
    contents: list[FeedCard]


class PracticalInfo(CamelModel):
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    age_limit: str | None = None
    preparation: str | None = None
    price: str | None = None


class ShareInfo(CamelModel):
    url: str
    text: str = "me;nu에서 확인하세요"


class DetailReaction(MyReaction):
    bookmarked: bool = False


class ContentDetail(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"contentId": 301, "topicId": 1, "title": "로컬 샘플", "productionType": "human", "sourceUrl": "https://example.com/article", "publisher": "MeNu 로컬 테스트", "publishedAt": "2026-09-17T00:00:00Z", "displayMode": "link_excerpt", "excerpt": "허용된 발췌", "body": None, "contentType": "article", "subtopicIds": [12], "practicalInfo": {"startsAt": None, "endsAt": None, "ageLimit": None, "preparation": None, "price": None}, "notices": [], "isPromotional": False, "share": {"url": "https://example.com/article", "text": "me;nu에서 확인하세요"}, "myReaction": {"liked": False, "preference": None, "bookmarked": False}}})
    content_id: int
    topic_id: int
    title: str
    image_url: str | None = None
    slides: list[ContentSlideResponse] = Field(default_factory=list)
    production_type: Literal["human", "ai", "hybrid"]
    source_url: str
    sources: list[ContentSourceResponse] = Field(default_factory=list)
    publisher: str
    published_at: datetime | None
    display_mode: str = "link_excerpt"
    excerpt: str | None = None
    body: str | None = None
    content_type: str | None = None
    subtopic_ids: list[int] = Field(default_factory=list)
    subtopic_names: list[str] = Field(default_factory=list)
    practical_info: PracticalInfo = Field(default_factory=PracticalInfo)
    notices: list[dict[str, str]] = Field(default_factory=list)
    is_promotional: bool = False
    share: ShareInfo
    my_reaction: DetailReaction = Field(default_factory=DetailReaction)

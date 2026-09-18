from pydantic import BaseModel, Field


class ContentBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    summary: str = Field(min_length=1, max_length=500)


class ContentCreate(ContentBase):
    pass


class ContentReplace(ContentBase):
    pass


class ContentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    summary: str | None = Field(default=None, min_length=1, max_length=500)


class ContentResponse(ContentBase):
    id: int


class ContentListResponse(BaseModel):
    items: list[ContentResponse]
    total: int

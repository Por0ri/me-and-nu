from fastapi import APIRouter, HTTPException, Query, Response, status

from app.mocks.content_store import mock_contents
from app.schemas.content import (
    ContentCreate,
    ContentListResponse,
    ContentReplace,
    ContentResponse,
    ContentUpdate,
)

router = APIRouter()


def find_content_index(content_id: int) -> int:
    for index, content in enumerate(mock_contents):
        if content["id"] == content_id:
            return index

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="콘텐츠를 찾을 수 없습니다.",
    )


@router.get("", response_model=ContentListResponse)
async def get_contents(
    category: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    filtered_contents = mock_contents

    if category:
        filtered_contents = [
            content
            for content in mock_contents
            if content["category"] == category
        ]

    return {
        "items": filtered_contents[offset : offset + limit],
        "total": len(filtered_contents),
    }


@router.get("/{content_id}", response_model=ContentResponse)
async def get_content(content_id: int):
    index = find_content_index(content_id)
    return mock_contents[index]


@router.post(
    "",
    response_model=ContentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_content(request: ContentCreate):
    next_id = max(
        (content["id"] for content in mock_contents),
        default=0,
    ) + 1

    new_content = {
        "id": next_id,
        **request.model_dump(),
    }

    mock_contents.append(new_content)
    return new_content


@router.put("/{content_id}", response_model=ContentResponse)
async def replace_content(
    content_id: int,
    request: ContentReplace,
):
    index = find_content_index(content_id)

    replaced_content = {
        "id": content_id,
        **request.model_dump(),
    }

    mock_contents[index] = replaced_content
    return replaced_content


@router.patch("/{content_id}", response_model=ContentResponse)
async def update_content(
    content_id: int,
    request: ContentUpdate,
):
    index = find_content_index(content_id)
    update_data = request.model_dump(exclude_unset=True)
    mock_contents[index].update(update_data)

    return mock_contents[index]


@router.delete(
    "/{content_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_content(content_id: int):
    index = find_content_index(content_id)
    mock_contents.pop(index)

    return Response(status_code=status.HTTP_204_NO_CONTENT)

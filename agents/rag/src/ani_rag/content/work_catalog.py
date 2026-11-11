"""작품 목록 읽기 — 글감의 작품 번호는 정보사전이 정해서 넘기므로 제목으로 작품을 찾지 않는다"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from typing import Any

from ani_rag.config import load_settings
from ani_rag.content.models import ContentPackage

_DONE = ("", "완료")  # title_ko_check 가 이 값이면 사람이 확인을 끝낸 제목


@lru_cache
def load_work_catalog() -> list[dict[str, Any]]:
    """작품 목록. 확인이 끝나지 않은 한국어 제목은 비운다."""
    path = load_settings().catalog_dir / "menu_works.csv"
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [
        {
            **row,
            "menu_work_id": int(row["menu_work_id"]),
            "title_ko": row["title_ko"] if row.get("title_ko_check", "") in _DONE else "",
        }
        for row in rows
    ]


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).casefold()


@lru_cache
def _titles() -> tuple[tuple[str, int], ...]:
    pairs = [(_key(r["title_ko"]), r["menu_work_id"]) for r in load_work_catalog() if r["title_ko"]]
    return tuple(sorted((p for p in pairs if len(p[0]) >= 2), key=lambda p: -len(p[0])))


def resolve_title_work_ids(title: str, source_url: str | None = None) -> list[int]:
    """글 안에 이름이 나오는 작품 번호 (카드와 작품을 잇는 데만 쓴다)."""
    text = _key(title)
    found: list[int] = []
    for key, work_id in _titles():
        if key in text and work_id not in found:
            found.append(work_id)
    return found


def resolve_menu_work_ids(package: ContentPackage) -> list[int]:
    return list(dict.fromkeys(package.menu_work_ids))


def attach_menu_work_ids(package: ContentPackage) -> ContentPackage:
    package.menu_work_ids = resolve_menu_work_ids(package)
    return package

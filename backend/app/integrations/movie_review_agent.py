import asyncio
import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any


DEFAULT_MODULE_PATH = "agent.movie.movie_review_agent"


@dataclass(frozen=True)
class MovieReviewAgentAdapter:
    """기존 영화 리뷰 에이전트를 Runner용 인터페이스로 변환합니다."""

    module: ModuleType

    @classmethod
    def load_default(cls) -> "MovieReviewAgentAdapter":
        # 에이전트 모듈은 import 시 agent/.env의 키를 검사하므로 지연 로딩합니다.
        return cls(importlib.import_module(DEFAULT_MODULE_PATH))

    @property
    def version(self) -> str:
        return str(getattr(self.module, "VERSION", "unknown"))

    async def fetch_material(self, title: str, year: int) -> Any | None:
        # requests 기반 동기 수집 함수가 async 서버 루프를 막지 않도록 분리합니다.
        return await asyncio.to_thread(
            self.module.fetch_material,
            title,
            year,
        )

    async def produce(self, material: Any) -> Any:
        return await self.module.produce(material)

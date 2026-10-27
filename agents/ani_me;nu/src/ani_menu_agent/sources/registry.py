"""JSON 출처 레지스트리 로더."""

import json
from pathlib import Path

from ani_menu_agent.sources.models import SourceRegistry

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "sources.json"


def load_source_registry(path: Path | None = None) -> SourceRegistry:
    """설정을 읽고 구조와 출처 상태 규칙을 검증합니다."""
    registry_path = path or DEFAULT_REGISTRY_PATH
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    return SourceRegistry.model_validate(payload)

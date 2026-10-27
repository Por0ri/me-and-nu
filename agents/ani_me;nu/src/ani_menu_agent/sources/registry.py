"""JSON 출처 레지스트리 로더."""

import json
from pathlib import Path

from ani_menu_agent.sources.models import SourceRegistry

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "sources.json"
DEFAULT_TYPE_MAP_PATH = DEFAULT_REGISTRY_PATH.with_name("source_type_map.json")


def load_source_registry(path: Path | None = None) -> SourceRegistry:
    """설정을 읽고 구조와 출처 상태 규칙을 검증합니다."""
    registry_path = path or DEFAULT_REGISTRY_PATH
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    type_map_path = registry_path.with_name("source_type_map.json")
    if type_map_path.exists():
        type_map = json.loads(type_map_path.read_text(encoding="utf-8")).get("sources", {})
        registry_ids = {source["id"] for source in payload.get("sources", [])}
        unknown_ids = set(type_map) - registry_ids
        if unknown_ids:
            raise ValueError(
                "유형 매핑에 레지스트리에 없는 source ID가 있습니다: "
                + ", ".join(sorted(unknown_ids))
            )
        for source in payload.get("sources", []):
            source["content_types"] = type_map.get(source["id"], [])
    return SourceRegistry.model_validate(payload)

"""수집 출처 정의와 레지스트리."""

from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.sources.registry import load_source_registry

__all__ = ["Source", "SourceRegistry", "load_source_registry"]

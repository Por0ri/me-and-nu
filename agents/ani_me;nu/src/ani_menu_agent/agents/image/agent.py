"""이미지 파일 분석 결과와 출처 메타데이터를 저장하는 LangGraph 노드."""

from datetime import UTC, datetime
from pathlib import Path
from urllib.robotparser import RobotFileParser

from ani_menu_agent.agents.common import append_trace
from ani_menu_agent.agents.image.download import download_image
from ani_menu_agent.models import ContentPackage, RightsStatus
from ani_menu_agent.policy import POLICY
from ani_menu_agent.state import ContentState
from ani_menu_agent.storage import AgentRepository
from ani_menu_agent.vision import detect_alpha_subject_box


def image_curator(state: ContentState) -> ContentState:
    package = ContentPackage.model_validate(state["package"])
    repository = state.get("repository") or AgentRepository()
    download_errors: list[str] = []
    robots_cache: dict[str, RobotFileParser | None] = {}
    for index, image in enumerate(package.images):
        image_sources = list(package.sources)
        if image.provenance_source and all(
            str(source.url) != str(image.provenance_source.url) for source in image_sources
        ):
            image_sources.append(image.provenance_source)
        if (
            POLICY.allow_public_official_image_risk
            and image.rights_status == RightsStatus.NEEDS_REVIEW
            and image.official
            and image.public_official_source_matches(image_sources)
        ):
            source = next(
                source
                for source in image_sources
                if source.is_official
                and source.role == "primary"
                and image.public_official_source_matches([source])
            )
            image = image.model_copy(
                update={
                    "rights_status": RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED,
                    "rights_evidence_url": image.source_url,
                    "rights_checked_at": image.rights_checked_at or datetime.now(UTC).isoformat(),
                    "attribution_text": image.attribution_text.strip()
                    or f"공식 이미지 출처: {source.publisher} · {source.title}",
                }
            )
        elif (
            POLICY.allow_public_editorial_image_risk
            and image.rights_status == RightsStatus.NEEDS_REVIEW
            and not image.official
            and image.public_editorial_source_matches(image_sources)
        ):
            source = next(
                source
                for source in image_sources
                if source.role in {"editorial", "reference"}
                and image.public_editorial_source_matches([source])
            )
            image = image.model_copy(
                update={
                    "rights_status": RightsStatus.PUBLIC_EDITORIAL_RISK_ACCEPTED,
                    "rights_evidence_url": image.source_url,
                    "rights_checked_at": image.rights_checked_at or datetime.now(UTC).isoformat(),
                    "attribution_text": image.attribution_text.strip()
                    or f"이미지 출처: {source.publisher} · {source.title}",
                }
            )
        local_path = Path(image.local_path) if image.local_path else None
        if local_path and not local_path.is_absolute():
            local_path = Path.cwd() / local_path

        if local_path and local_path.is_file():
            image = image.model_copy(
                update={
                    "local_path": str(local_path),
                    "download_status": "available",
                    "download_error": None,
                }
            )
        elif image.storage_key and repository.get_image_bytes(image.storage_key):
            image = image.model_copy(
                update={"download_status": "downloaded", "download_error": None}
            )
        elif image.url and not image.eligible_for_editorial_use(
            image_sources,
            allow_risk_accepted=(
                POLICY.allow_public_official_image_risk
                or POLICY.allow_public_editorial_image_risk
            ),
        ):
            image = image.model_copy(
                update={
                    "download_status": "blocked_rights",
                    "download_error": "이용 허가 범위가 확인되지 않아 다운로드하지 않았습니다.",
                }
            )
        elif image.url and state.get("download_images", False):
            try:
                image = download_image(package, image, robots_cache=robots_cache)
            except Exception as exc:
                message = str(exc)[:300] or exc.__class__.__name__
                image = image.model_copy(
                    update={"download_status": "failed", "download_error": message}
                )
                download_errors.append(f"{image.id}: {message}")
        elif image.url:
            image = image.model_copy(update={"download_status": "pending"})
        else:
            image = image.model_copy(
                update={
                    "download_status": "failed",
                    "download_error": "로컬 이미지 파일을 찾을 수 없습니다.",
                }
            )
            download_errors.append(f"{image.id}: 로컬 이미지 파일을 찾을 수 없습니다.")

        if image.has_alpha and image.local_path and image.subject_box is None:
            path = Path(image.local_path)
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.is_file():
                image.subject_box = detect_alpha_subject_box(path)
        package.images[index] = image
    persisted = repository.persist_images(package)
    result: ContentState = {
        "trace": append_trace(state, "image_curator"),
        "status": "images_curated",
        "persisted_image_count": persisted,
        "package": package.model_dump(mode="json"),
    }
    if download_errors:
        result["image_download_errors"] = download_errors
    return result

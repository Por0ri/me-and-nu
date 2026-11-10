"""Bounded OCR for information embedded in RE:CAL event detail graphics."""

from __future__ import annotations

import base64
from io import BytesIO
from time import perf_counter
import httpx
from PIL import Image

from ani_menu_agent.config import get_settings
from ani_menu_agent.sources.http_policy import pace_host
from ani_menu_agent.sources.models import Source
from ani_menu_agent.sources.page_collector import SourcePageCapture, _validate_url

MAX_OCR_IMAGES = 2
MAX_OCR_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_OCR_IMAGE_SIDE = 1800


def _prepare_image(raw: bytes) -> bytes:
    with Image.open(BytesIO(raw)) as source_image:
        image = source_image.convert("RGB")
        image.thumbnail((MAX_OCR_IMAGE_SIDE, MAX_OCR_IMAGE_SIDE), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
        return output.getvalue()


def extract_page_image_text(
    capture: SourcePageCapture,
    source: Source,
    *,
    timeout: float = 20.0,
) -> tuple[list[dict], list[dict]]:
    """Read up to two page-owned images with the configured vision-capable model.

    Returns provenance-bearing OCR records and token-usage records. This is
    intentionally limited to RE:CAL event/music detail pages; it does not OCR
    arbitrary artwork or images whose collection was explicitly excluded.
    """
    settings = get_settings()
    if source.id not in {"recal_kr", "recal_music"} or not settings.image_ocr_enabled:
        return [], []

    image_urls = [str(url) for url in capture.image_urls[:MAX_OCR_IMAGES]]
    if not image_urls:
        return [], []
    if not settings.openai_api_key:
        return [
            {
                "image_url": image_url,
                "status": "skipped_no_api_key",
                "extracted_text": "",
                "note": "OCR 미실행: API 키가 없어 상세 이미지 문구를 추출하지 않음.",
            }
            for image_url in image_urls
        ], []

    # Import only when OCR is enabled and an eligible page actually has images.
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(model=settings.model_name, api_key=settings.openai_api_key)
    records: list[dict] = []
    usage_records: list[dict] = []
    from ani_menu_agent.generation import estimate_gpt_6_luna_cost

    headers = {"User-Agent": "me-nu-content-agent/0.3 (+approved-source collector)"}
    for image_index, image_url in enumerate(image_urls, start=1):
        record = {
            "image_url": image_url,
            "image_index": image_index,
            "status": "unreadable",
            "extracted_text": "",
            "note": "",
        }
        try:
            _validate_url(image_url, source)
            pace_host(image_url, source.min_interval_seconds)
            with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
                response = client.get(image_url)
            response.raise_for_status()
            if len(response.content) > MAX_OCR_DOWNLOAD_BYTES:
                raise ValueError("OCR 이미지가 8MB 제한을 초과함.")
            prepared = _prepare_image(response.content)
            data_url = "data:image/jpeg;base64," + base64.b64encode(prepared).decode("ascii")
            started = perf_counter()
            response_message = model.invoke(
                [
                    SystemMessage(
                        content=(
                            "이미지에 인쇄된 정보를 원문 그대로 전사하는 OCR 작업자다. "
                            "이미지의 내용을 요약하거나 추론하지 않는다. 한국어·일본어·영어를 "
                            "보이는 대로 옮기고, 읽히지 않는 구간은 [판독 불확실]로 표시한다. "
                            "각 글 묶음 앞에 이미지 내 위치([상단], [중앙], [하단], [좌측], "
                            "[우측] 중 해당 위치)를 붙여라. 장식 문구와 홍보 문구도 구분 없이 "
                            "전사하되, 그림 설명은 쓰지 않는다."
                        )
                    ),
                    HumanMessage(
                        content=[
                            {
                                "type": "text",
                                "text": (
                                    f"출처 페이지: {capture.url}\n"
                                    f"이미지 URL: {image_url}\n"
                                    "이 이미지에 들어 있는 글자만 원문 순서대로 전사해라."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url, "detail": "high"},
                            },
                        ]
                    ),
                ]
            )
            extracted = str(response_message.content or "").strip()
            record.update(
                {
                    "status": "extracted" if extracted else "unreadable",
                    "extracted_text": extracted[:12_000],
                    "note": "AI OCR 초안. 날짜·장소·가격·예매 조건은 원문 HTML 또는 공식 예매처와 대조해야 함.",
                }
            )
            usage = getattr(response_message, "usage_metadata", None) or {}
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)
            input_details = usage.get("input_token_details") or {}
            cached_tokens = int(input_details.get("cache_read", 0) or 0)
            cache_write_tokens = int(
                input_details.get("cache_write", input_details.get("cache_creation", 0)) or 0
            )
            if input_tokens or output_tokens:
                usage_records.append(
                    {
                        "model": settings.model_name,
                        "stage": "event_detail_image_ocr",
                        "source_url": str(capture.url),
                        "image_url": image_url,
                        "image_index": image_index,
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached_tokens,
                        "cache_write_tokens": cache_write_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": int(
                            usage.get("total_tokens", input_tokens + output_tokens) or 0
                        ),
                        "estimated_cost_usd": estimate_gpt_6_luna_cost(
                            input_tokens, cached_tokens, cache_write_tokens, output_tokens
                        ),
                        "elapsed_seconds": round(perf_counter() - started, 3),
                    }
                )
        except Exception as exc:  # OCR failure must not erase the page/article capture.
            record["note"] = f"OCR 미완료: {type(exc).__name__}: {str(exc)[:240]}"
        records.append(record)
    return records, usage_records

"""Provider evidence gaps must not turn attempted calls into zero-cost work."""

from copy import deepcopy
import socket
from types import SimpleNamespace

import pytest

from ani_menu_agent import extraction, work_match_fallback
from ani_menu_agent.generation import estimate_gpt_6_luna_cost
from ani_menu_agent.sources import image_ocr, link_review
from ani_menu_agent.sources.models import Source, SourceRegistry
from ani_menu_agent.sources.page_collector import PageLinkCandidate


KINDS = ("ocr", "link", "extraction", "work_match")
VALID_USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "total_tokens": 120,
    "input_token_details": {"cache_read": 10, "cache_write": 5},
}
STAGES = {
    "ocr": "event_detail_image_ocr",
    "link": "source_link_review",
    "extraction": "fact_extraction",
    "work_match": "work_match_fallback",
}


@pytest.fixture(autouse=True)
def forbid_live_calls(monkeypatch):
    for key in ("OPENAI_API_KEY", "LLM_KEY"):
        monkeypatch.setenv(key, "")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Network access is forbidden in provider usage tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def install_helper(monkeypatch, kind, usage, *, error=None):
    settings = SimpleNamespace(
        openai_api_key="mock-only-not-a-provider-key",
        model_name="gpt-6-luna",
        image_ocr_enabled=True,
        region_policy="kr_first",
    )
    records = [{"stage": "previous", "estimated_cost_usd": 0.125}]
    model = SimpleNamespace(calls=0, response=None)

    def invoke(_messages):
        model.calls += 1
        if error is not None:
            raise error
        return model.response

    model.invoke = invoke
    model.with_structured_output = lambda *_a, **_kw: model
    raw = SimpleNamespace(usage_metadata=deepcopy(usage))
    audit = {}

    if kind == "ocr":
        import langchain_openai

        module = image_ocr
        constructor_module = langchain_openai
        capture = SimpleNamespace(
            url="https://recal.kr/event/1", image_urls=["https://recal.kr/image.jpg"]
        )
        source = SimpleNamespace(id="recal_kr", min_interval_seconds=0)
        model.response = SimpleNamespace(content="2026년 12월 1일", usage_metadata=deepcopy(usage))
        monkeypatch.setattr(module, "_validate_url", lambda *_a: None)
        monkeypatch.setattr(module, "pace_host", lambda *_a: None)
        monkeypatch.setattr(module, "_prepare_image", lambda _data: b"mock-image")

        class Client:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def get(self, _url):
                return SimpleNamespace(content=b"mock-image", raise_for_status=lambda: None)

        monkeypatch.setattr(module.httpx, "Client", Client)

        def run():
            result, new_records = module.extract_page_image_text(capture, source)
            records[:] = new_records
            return result

    elif kind == "link":
        module = constructor_module = link_review
        candidate = PageLinkCandidate(
            url="https://example.test/article",
            discovery_method="anchor",
            label="Details",
            target_page_title="Anime event",
            target_excerpt="Anime event confirmed",
        )
        model.response = {
            "raw": raw,
            "parsed": module.LinkReviewBatch(items=[module.LinkReviewItem(
                candidate_url=str(candidate.url), same_work=True,
                relationship="event_detail", confidence=0.9,
                evidence_quote="Anime event confirmed",
            )]),
        }

        def run():
            return module.classify_link_candidates(
                [candidate], source_role="primary", context_title="Anime event",
                usage_records=records, allow_llm=True,
            )

    elif kind == "extraction":
        module = constructor_module = extraction
        source = Source(
            id="test_official", name="Test official", url="https://example.test/",
            category="official_anime", role="primary", collection="html", status="active", region="JP",
        )
        monkeypatch.setattr(module, "load_source_registry", lambda: SourceRegistry(version=1, sources=[source]))
        model.response = {
            "raw": raw,
            "parsed": module.PageFactExtraction(topic="Anime news", facts=[
                module.FactCandidate(statement="A new event was announced.", evidence_quote="New event announced")
            ]),
        }

        def run():
            package, record = module.extract_page_payload({
                "source_id": source.id, "title": "Anime news",
                "url": "https://example.test/article", "text": "New event announced",
                "fetched_at": "2026-11-26T00:00:00+00:00",
            })
            records[:] = [record]
            return package

    else:
        module = constructor_module = work_match_fallback
        monkeypatch.setattr(module, "_candidate_catalog_rows", lambda *_a, **_kw: [])
        monkeypatch.setattr(module, "record_unresolved_source_title", lambda *_a, **_kw: None)
        model.response = {
            "raw": raw,
            "parsed": module.WorkIdentityDecision(action="unresolved", confidence=0.3),
        }

        def run():
            return module.resolve_unmatched_work_with_llm(
                titles=["Unknown anime"], source_url="https://example.test/article",
                usage_records=records, audit=audit,
            )

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(constructor_module, "ChatOpenAI", lambda **_kw: model)
    return SimpleNamespace(
        run=run, model=model, records=records, module=module,
        constructor_module=constructor_module, settings=settings, audit=audit,
    )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("usage", [
    None, {}, {"input_tokens": 100}, {"output_tokens": 20},
    {"input_tokens": "100", "output_tokens": 20},
    {"input_tokens": 0, "output_tokens": 0},
    {"input_tokens": -1, "output_tokens": 20},
])
def test_attempted_call_without_complete_metadata_is_unknown(monkeypatch, kind, usage):
    helper = install_helper(monkeypatch, kind, usage)
    result = helper.run()

    assert helper.model.calls == 1
    assert len(helper.records) == (2 if kind in {"link", "work_match"} else 1)
    receipt = helper.records[-1]
    assert receipt["stage"] == STAGES[kind]
    assert receipt["invocation_attempted"] is True
    assert receipt["usage_complete"] is False
    assert receipt["reason"] == "provider_usage_unavailable"
    assert "estimated_cost_usd" not in receipt
    if kind in {"link", "work_match"}:
        assert helper.records[0] == {"stage": "previous", "estimated_cost_usd": 0.125}
    if kind == "ocr":
        assert result[0]["status"] == "extracted"
    elif kind == "link":
        assert result[0].target_work_match is True
        assert result[0].link_classification == "related_page"
    elif kind == "extraction":
        assert result.facts[0].verified is True


@pytest.mark.parametrize("kind", KINDS)
def test_valid_receipt_replaces_attempt_marker_without_double_counting(monkeypatch, kind):
    helper = install_helper(monkeypatch, kind, VALID_USAGE)
    helper.run()

    assert helper.model.calls == 1
    assert len(helper.records) == (2 if kind in {"link", "work_match"} else 1)
    receipt = helper.records[-1]
    assert receipt["stage"] == STAGES[kind]
    assert receipt["input_tokens"] == 100
    assert receipt["output_tokens"] == 20
    assert receipt["cached_input_tokens"] == 10
    assert receipt["cache_write_tokens"] == 5
    assert receipt["total_tokens"] == 120
    assert receipt["estimated_cost_usd"] == estimate_gpt_6_luna_cost(100, 10, 5, 20)
    assert "usage_complete" not in receipt


@pytest.mark.parametrize("kind", ["ocr", "link", "work_match"])
def test_swallowed_invoke_failure_preserves_unknown_receipt(monkeypatch, kind):
    helper = install_helper(monkeypatch, kind, VALID_USAGE, error=RuntimeError("mock invoke failed"))
    helper.run()

    assert helper.model.calls == 1
    assert len(helper.records) == (1 if kind == "ocr" else 2)
    assert helper.records[-1]["usage_complete"] is False
    assert "estimated_cost_usd" not in helper.records[-1]
    if kind == "work_match":
        assert helper.audit["status"] == "llm_error"


def test_extraction_exception_still_propagates_unchanged(monkeypatch):
    error = RuntimeError("mock extraction failed")
    helper = install_helper(monkeypatch, "extraction", VALID_USAGE, error=error)
    with pytest.raises(RuntimeError) as raised:
        helper.run()
    assert raised.value is error
    assert helper.model.calls == 1


@pytest.mark.parametrize("kind", ["ocr", "link", "work_match"])
def test_disabled_call_does_not_claim_unknown_spend(monkeypatch, kind):
    helper = install_helper(monkeypatch, kind, None)
    helper.settings.openai_api_key = ""
    helper.run()
    assert helper.model.calls == 0
    assert helper.records == ([] if kind == "ocr" else [{"stage": "previous", "estimated_cost_usd": 0.125}])


@pytest.mark.parametrize("kind", KINDS)
def test_constructor_failure_is_not_recorded_as_invocation(monkeypatch, kind):
    helper = install_helper(monkeypatch, kind, None)

    def failed_constructor(**_kwargs):
        raise RuntimeError("mock constructor failed")

    monkeypatch.setattr(helper.constructor_module, "ChatOpenAI", failed_constructor)
    with pytest.raises(RuntimeError, match="mock constructor failed"):
        helper.run()
    assert helper.model.calls == 0
    assert helper.records == [{"stage": "previous", "estimated_cost_usd": 0.125}]


@pytest.mark.parametrize("kind", ["link", "work_match"])
def test_preinvoke_serialization_failure_keeps_existing_fallback_behavior(monkeypatch, kind):
    helper = install_helper(monkeypatch, kind, None)

    def invalid_payload(*_args, **_kwargs):
        raise TypeError("mock serialization failed")

    monkeypatch.setattr(helper.module.json, "dumps", invalid_payload)
    helper.run()
    assert helper.model.calls == 0
    assert helper.records == [{"stage": "previous", "estimated_cost_usd": 0.125}]


def test_ocr_failure_before_invoke_has_no_unknown_cost(monkeypatch):
    helper = install_helper(monkeypatch, "ocr", None)

    def invalid_image(_data):
        raise ValueError("mock invalid image")

    monkeypatch.setattr(image_ocr, "_prepare_image", invalid_image)
    result = helper.run()
    assert helper.model.calls == 0
    assert helper.records == []
    assert result[0]["status"] == "unreadable"

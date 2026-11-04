from types import SimpleNamespace

import pytest

from ani_menu_agent import work_match_fallback as fallback
from ani_menu_agent.models import WorkMatchEvidence


def _catalog_row(work_id: int, title: str) -> dict:
    return {
        "menu_work_id": work_id,
        "title_ko": title,
        "title_romaji": title,
        "title_english": title,
        "title_native": "",
        "first_year": "2002",
    }


def _install_model(monkeypatch, decision, *, usage=None) -> None:
    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return self

        def invoke(self, _messages):
            return {
                "parsed": decision,
                "raw": SimpleNamespace(
                    usage_metadata=usage
                    or {
                        "input_tokens": 1000,
                        "output_tokens": 100,
                        "total_tokens": 1100,
                        "input_token_details": {"cache_read": 100},
                    }
                ),
            }

    monkeypatch.setattr(
        fallback,
        "ChatOpenAI",
        lambda **_kwargs: FakeModel(),
    )
    monkeypatch.setattr(
        fallback,
        "get_settings",
        lambda: SimpleNamespace(openai_api_key="test-key", model_name="gpt-6-luna"),
    )


def test_fallback_matches_only_catalog_id_and_records_cost(monkeypatch) -> None:
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [_catalog_row(7, "Naruto")])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    monkeypatch.setattr(
        fallback,
        "register_unmatched_work_from_kitsu",
        lambda *_args, **_kwargs: None,
    )
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="match_existing",
            catalog_matches=[
                fallback.CatalogWorkSelection(menu_work_id=7, catalog_title="Naruto")
            ],
            evidence_title="Naruto Shippuden",
            confidence=0.96,
            rationale="The title names the Naruto sequel entry.",
        ),
    )
    usage: list[dict] = []
    audit: dict = {}

    matches = fallback.resolve_unmatched_work_with_llm(
        titles=["Naruto Shippuden"], usage_records=usage, audit=audit
    )

    assert [match.menu_work_id for match in matches] == [7]
    assert matches[0].basis == "llm_title_match"
    assert len(usage) == 1
    assert usage[0]["stage"] == "work_match_fallback"
    assert usage[0]["estimated_cost_usd"] > 0
    assert audit["status"] == "matched"


def test_existing_parent_mapping_uses_kitsu_title_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [_catalog_row(7, "Naruto")])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="match_existing",
            catalog_matches=[
                fallback.CatalogWorkSelection(menu_work_id=7, catalog_title="Naruto")
            ],
            evidence_title="Naruto Shippuden",
            confidence=0.96,
        ),
    )
    calls = []
    confirmed_entry = WorkMatchEvidence(
        menu_work_id=7,
        basis="kitsu_catalog_entry_title",
        matched_value="Naruto Shippuden",
        evidence_url="https://kitsu.io/anime/12345",
        confidence=0.97,
    )
    monkeypatch.setattr(
        fallback,
        "register_unmatched_work_from_kitsu",
        lambda titles, **kwargs: calls.append((titles, kwargs)) or confirmed_entry,
    )

    matches = fallback.resolve_unmatched_work_with_llm(titles=["Naruto Shippuden"])

    assert matches == [confirmed_entry]
    assert calls == [(["Naruto Shippuden"], {"existing_menu_work_id": 7})]


def test_shortlist_keeps_canonical_title_when_many_entry_titles_exist(monkeypatch) -> None:
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [_catalog_row(11, "Naruto")])
    monkeypatch.setattr(
        fallback,
        "load_work_entries",
        lambda: [
            {
                "menu_work_id": 11,
                "title_ko": "",
                "title_romaji": f"Naruto Movie Special Episode {index}",
                "title_english": f"NARUTO: Long Movie Special Entry {index}",
                "title_native": "",
                "year": "2005",
            }
            for index in range(1, 12)
        ],
    )

    candidates = fallback._candidate_catalog_rows(["Naruto Shippuden"])

    assert candidates[0]["menu_work_id"] == 11
    assert "Naruto" in candidates[0]["titles"]


def test_shortlist_uses_body_alias_and_limits_fuzzy_work_to_shared_tokens(monkeypatch) -> None:
    catalog = [_catalog_row(7, "Naruto")] + [
        _catalog_row(work_id, f"Unrelated Work {work_id}")
        for work_id in range(100, 300)
    ]
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: catalog)
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    monkeypatch.setattr(fallback, "load_work_external_entries", lambda: [])
    scored: list[tuple[str, str]] = []
    original_score = fallback._retrieval_score

    def track_score(query: str, alias: str) -> float:
        scored.append((query, alias))
        return original_score(query, alias)

    monkeypatch.setattr(fallback, "_retrieval_score", track_score)

    candidates = fallback._candidate_catalog_rows(
        ["A new announcement with no work title"],
        body_queries=["The article is about Naruto Shippuden and its new season."],
    )

    assert candidates[0]["menu_work_id"] == 7
    assert "Naruto" in candidates[0]["titles"]
    assert len(scored) < 20


@pytest.mark.parametrize(
    "decision",
    [
        fallback.WorkIdentityDecision(
            action="match_existing",
            catalog_matches=[
                fallback.CatalogWorkSelection(menu_work_id=999, catalog_title="Naruto")
            ],
            evidence_title="Naruto Shippuden",
            confidence=0.99,
        ),
        fallback.WorkIdentityDecision(
            action="match_existing",
            catalog_matches=[
                fallback.CatalogWorkSelection(menu_work_id=7, catalog_title="Naruto")
            ],
            evidence_title="A title absent from the input",
            confidence=0.99,
        ),
        fallback.WorkIdentityDecision(
            action="match_existing",
            catalog_matches=[
                fallback.CatalogWorkSelection(menu_work_id=7, catalog_title="Made-up alias")
            ],
            evidence_title="Naruto Shippuden",
            confidence=0.99,
        ),
    ],
)
def test_fallback_rejects_ungrounded_or_out_of_catalog_matches(monkeypatch, decision) -> None:
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [_catalog_row(7, "Naruto")])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    _install_model(monkeypatch, decision)

    assert fallback.resolve_unmatched_work_with_llm(titles=["Naruto Shippuden"]) == []


def test_low_confidence_decision_stays_unresolved(monkeypatch) -> None:
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [_catalog_row(7, "Naruto")])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="match_existing",
            catalog_matches=[
                fallback.CatalogWorkSelection(menu_work_id=7, catalog_title="Naruto")
            ],
            evidence_title="Naruto Shippuden",
            confidence=0.79,
        ),
    )

    audit: dict = {}
    assert fallback.resolve_unmatched_work_with_llm(
        titles=["Naruto Shippuden"], audit=audit
    ) == []
    assert audit["status"] == "low_confidence"


def test_fallback_rejects_an_unrelated_work_alias_without_kitsu_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [_catalog_row(7, "Flowers of Evil")])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    monkeypatch.setattr(fallback, "load_work_external_entries", lambda: [])
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="match_existing",
            catalog_matches=[
                fallback.CatalogWorkSelection(menu_work_id=7, catalog_title="Flowers of Evil")
            ],
            evidence_title="Blood on the Tracks",
            evidence_quote="Shattering the Self: A conversation with Blood on the Tracks creator",
            confidence=0.99,
        ),
    )
    monkeypatch.setattr(fallback, "register_unmatched_work_from_kitsu", lambda *_a, **_k: None)

    audit: dict = {}
    matches = fallback.resolve_unmatched_work_with_llm(
        titles=["Shattering the Self: A conversation with Blood on the Tracks creator"],
        body_excerpt="Shattering the Self: A conversation with Blood on the Tracks creator",
        audit=audit,
    )

    assert matches == []
    assert audit["status"] == "catalog_alias_not_confirmed"


def test_fallback_ignores_body_identity_from_generic_listing_pages(monkeypatch) -> None:
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [_catalog_row(7, "Doraemon")])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    monkeypatch.setattr(fallback, "load_work_external_entries", lambda: [])
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="match_existing",
            catalog_matches=[
                fallback.CatalogWorkSelection(menu_work_id=7, catalog_title="Doraemon")
            ],
            evidence_title="Doraemon",
            evidence_basis="body_excerpt",
            evidence_quote="The article is about Doraemon products from the official shop.",
            subject_relation="primary_subject",
            confidence=0.99,
        ),
    )

    audit: dict = {}
    matches = fallback.resolve_unmatched_work_with_llm(
        titles=["More goods features"],
        source_url="https://www.cinemacafe.net/special/14903/recent/goods",
        body_excerpt="The article is about Doraemon products from the official shop.",
        audit=audit,
    )

    assert matches == []
    assert audit["status"] == "body_context_skipped_listing_page"


def test_new_registration_requires_exact_source_title_and_catalog_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="register_new",
            evidence_title="Yu-Gi-Oh! GX",
            confidence=0.97,
            rationale="A distinct series title absent from the candidate catalog.",
        ),
    )
    registration_calls: list[list[str]] = []
    registered = WorkMatchEvidence(
        menu_work_id=2434,
        basis="kitsu_catalog_title",
        matched_value="Yu-Gi-Oh! GX",
        evidence_url="https://kitsu.io/anime/12345",
        confidence=0.97,
    )
    monkeypatch.setattr(
        fallback,
        "register_unmatched_work_from_kitsu",
        lambda titles, **_kwargs: registration_calls.append(titles) or registered,
    )

    matches = fallback.resolve_unmatched_work_with_llm(titles=["Yu-Gi-Oh! GX"])

    assert matches == [registered]
    assert registration_calls == [["Yu-Gi-Oh! GX"]]


def test_new_registration_does_not_create_id_without_catalog_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="register_new",
            evidence_title="Yu-Gi-Oh! GX",
            confidence=0.97,
        ),
    )
    monkeypatch.setattr(
        fallback,
        "register_unmatched_work_from_kitsu",
        lambda _titles, **_kwargs: None,
    )

    assert fallback.resolve_unmatched_work_with_llm(titles=["Yu-Gi-Oh! GX"]) == []

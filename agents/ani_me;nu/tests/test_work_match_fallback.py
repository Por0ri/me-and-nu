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
            confidence=0.49,
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


@pytest.fixture
def character_check(monkeypatch):
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [
        _catalog_row(19, "Re:Zero"), _catalog_row(50, "無職転生")
    ])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    monkeypatch.setattr(fallback, "load_work_aliases", lambda: [])
    monkeypatch.setattr(fallback, "load_work_external_entries", lambda: [])
    monkeypatch.setattr(fallback, "register_unmatched_work_from_kitsu", lambda *a, **kw: None)
    return {
        "titles": ["ロキシー師匠のフィギュア登場！シルフィと並べて飾ろう"],
        "source_url": "https://example.org/products/roxy",
        "body_excerpt": "TVアニメ『無職転生』より、ロキシー師匠がグレイラット家にいる姿を再現したフィギュアが登場。",
        "character_hints": [WorkMatchEvidence(
            menu_work_id=19, basis="headline_character", matched_value="シルフィ", confidence=0.92
        )],
    }


@pytest.mark.parametrize("hint_id", [19, 50])
def test_original_body_can_correct_or_confirm_character_hint(monkeypatch, character_check, hint_id):
    character_check["character_hints"][0] = WorkMatchEvidence(
        menu_work_id=hint_id, basis="headline_character", matched_value="シルフィ", confidence=0.92
    )
    _install_model(monkeypatch, fallback.WorkIdentityDecision(
        action="match_existing", subject_scope="anime_work", subject_relation="primary_subject",
        catalog_matches=[fallback.CatalogWorkSelection(menu_work_id=50, catalog_title="無職転生")],
        evidence_title="無職転生", evidence_basis="body_excerpt",
        evidence_quote=character_check["body_excerpt"], confidence=0.93,
    ))
    usage = []
    audit = {}
    match, = fallback.resolve_unmatched_work_with_llm(**character_check, usage_records=usage, audit=audit)
    assert (match.menu_work_id, match.basis, str(match.evidence_url)) == (
        50, "llm_body_context", character_check["source_url"]
    )
    assert audit["status"] == "matched"
    assert [record["stage"] for record in usage] == ["work_match_fallback"]


@pytest.mark.parametrize(("title", "quote", "relation", "scope"), [
    ("シルフィ", "シルフィを紹介しました。", "primary_subject", "anime_work"),
    ("無職転生", "記事には存在しない無職転生の引用。", "primary_subject", "anime_work"),
    ("無職転生", "ここでは無職転生の関連情報にも触れます。", "incidental_mention", "anime_work"),
    ("無職転生", "ここでは無職転生の関連情報にも触れます。", "primary_subject", "unclear"),
])
def test_character_hint_needs_actual_primary_work_evidence(
    monkeypatch, character_check, title, quote, relation, scope
):
    character_check["body_excerpt"] += "シルフィを紹介しました。ここでは無職転生の関連情報にも触れます。"
    _install_model(monkeypatch, fallback.WorkIdentityDecision(
        action="match_existing", subject_scope=scope, subject_relation=relation,
        catalog_matches=[fallback.CatalogWorkSelection(menu_work_id=19, catalog_title="Re:Zero")],
        evidence_title=title, evidence_basis="body_excerpt", evidence_quote=quote, confidence=0.99,
    ))
    audit = {}
    assert fallback.resolve_unmatched_work_with_llm(**character_check, audit=audit) == []
    assert audit["status"] == "unconfirmed_character_work"


def test_character_hint_never_uses_unsupported_confident_headline_escape(monkeypatch, character_check):
    character_check["titles"] = ["無職転生 ロキシー師匠のフィギュア登場！シルフィと並べて飾ろう"]
    _install_model(monkeypatch, fallback.WorkIdentityDecision(
        action="match_existing", subject_scope="anime_work", subject_relation="primary_subject",
        catalog_matches=[fallback.CatalogWorkSelection(menu_work_id=19, catalog_title="Re:Zero")],
        evidence_title="無職転生", evidence_basis="headline_or_about",
        evidence_quote=character_check["body_excerpt"], confidence=0.99,
    ))
    audit = {}
    assert fallback.resolve_unmatched_work_with_llm(**character_check, audit=audit) == []
    assert audit["status"] == "catalog_alias_not_confirmed"


def test_character_hint_waits_for_article_without_paid_call(monkeypatch, character_check):
    character_check["body_excerpt"] = None
    monkeypatch.setattr(fallback, "get_settings", lambda: SimpleNamespace(openai_api_key="test-key"))
    monkeypatch.setattr(fallback, "ChatOpenAI", lambda **kw: pytest.fail("No article to confirm"))
    audit = {}
    assert fallback.resolve_unmatched_work_with_llm(**character_check, audit=audit) == []
    assert audit["status"] == "character_match_requires_article"


def test_grounded_new_title_gets_one_temporary_id_after_kitsu_miss(
    monkeypatch, tmp_path, request
) -> None:
    import csv
    import zlib

    from ani_menu_agent import work_catalog

    monkeypatch.delenv("ME_NU_WORK_REGISTRY_DIR", raising=False)
    monkeypatch.delenv("ME_NU_WORK_CATALOG_DIR", raising=False)
    monkeypatch.setattr(work_catalog, "_CATALOG_DIRECTORY", tmp_path)
    official = tmp_path / "catalog"
    official.mkdir()
    (official / "menu_works.csv").write_text("menu_work_id,title_ko\n", encoding="utf-8")
    monkeypatch.setattr(work_catalog, "_WORK_LIST_DIRECTORY", official)
    work_catalog.refresh_work_catalog_caches()
    request.addfinalizer(work_catalog.refresh_work_catalog_caches)
    monkeypatch.setattr(fallback, "load_work_catalog", lambda: [])
    monkeypatch.setattr(fallback, "load_work_entries", lambda: [])
    title = "Mysterious New Anime"
    url = "https://example.org/new-anime"
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="register_new",
            evidence_title=title,
            subject_scope="anime_work",
            subject_relation="primary_subject",
            confidence=0.97,
        ),
    )
    lookups = []

    def missing(titles, *, source_url, audit):
        lookups.append((titles, source_url))
        audit["status"] = "kitsu_match_missing"

    monkeypatch.setattr(fallback, "register_unmatched_work_from_kitsu", missing)
    expected = 2_000_000 + zlib.crc32(b"mysteriousnewanime") % 1_000_000_000
    for _ in range(2):
        audit = {}
        (match,) = fallback.resolve_unmatched_work_with_llm(
            titles=[title], source_url=url, audit=audit
        )
        assert match.menu_work_id == audit["menu_work_id"] == expected
        assert str(match.evidence_url) == url
    assert lookups == [([title], url)] * 2
    assert not (tmp_path / "menu_work_external_entries.csv").exists()
    with (official / "agent_works.csv").open(encoding="utf-8-sig", newline="") as stream:
        (row,) = csv.DictReader(stream)
    assert (int(row["menu_work_id"]), row["title_english"]) == (expected, title)
    assert (official / "menu_works.csv").read_text(encoding="utf-8") == "menu_work_id,title_ko\n"


@pytest.mark.parametrize(
    "status",
    ["kitsu_match_ambiguous", "new_work_logged_pending_review", "kitsu_lookup_unavailable"],
)
def test_new_title_never_falls_back_to_crc_when_kitsu_is_inconclusive(monkeypatch, status):
    title = "Mysterious New Anime"
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="register_new",
            evidence_title=title,
            subject_scope="anime_work",
            subject_relation="primary_subject",
            confidence=0.97,
        ),
    )
    monkeypatch.setattr(fallback, "_candidate_catalog_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(fallback, "record_unresolved_source_title", lambda *args: None)

    def inconclusive(_titles, *, source_url, audit):
        audit["status"] = status

    monkeypatch.setattr(fallback, "register_unmatched_work_from_kitsu", inconclusive)
    monkeypatch.setattr(
        fallback,
        "register_new_work_from_source_title",
        lambda *args, **kwargs: pytest.fail("CRC bypass"),
    )
    audit = {}
    assert (
        fallback.resolve_unmatched_work_with_llm(
            titles=[title], source_url="https://example.org/new", audit=audit
        )
        == []
    )
    assert audit["status"] == status


def test_new_anime_uses_confirmed_kitsu_id_instead_of_title_crc(monkeypatch):
    title = "Mysterious New Anime"
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="register_new",
            evidence_title=title,
            subject_scope="anime_work",
            subject_relation="primary_subject",
            confidence=0.97,
        ),
    )
    monkeypatch.setattr(fallback, "_candidate_catalog_rows", lambda *args, **kwargs: [])
    evidence = WorkMatchEvidence(
        menu_work_id=1_050_362,
        basis="kitsu_catalog_title",
        matched_value=title,
        evidence_url="https://kitsu.io/anime/50362",
        confidence=0.97,
    )
    monkeypatch.setattr(
        fallback, "register_unmatched_work_from_kitsu", lambda *args, **kwargs: evidence
    )
    monkeypatch.setattr(
        fallback, "register_new_work_from_source_title", lambda *args, **kwargs: pytest.fail("CRC")
    )
    assert fallback.resolve_unmatched_work_with_llm(
        titles=[title], source_url="https://example.org/new"
    ) == [evidence]


@pytest.mark.parametrize(
    ("relation", "url"),
    [
        ("unclear", "https://example.org/new"),
        ("incidental_mention", "https://example.org/new"),
        ("primary_subject", "file:///unknown"),
        ("primary_subject", "https://"),
    ],
)
def test_llm_new_work_requires_primary_subject_and_article_url_before_kitsu(
    monkeypatch, relation, url
):
    title = "Mysterious New Anime"
    _install_model(
        monkeypatch,
        fallback.WorkIdentityDecision(
            action="register_new",
            evidence_title=title,
            subject_scope="anime_work",
            subject_relation=relation,
            confidence=0.97,
        ),
    )
    monkeypatch.setattr(fallback, "_candidate_catalog_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(fallback, "record_unresolved_source_title", lambda *args: None)
    monkeypatch.setattr(
        fallback,
        "register_unmatched_work_from_kitsu",
        lambda *args, **kwargs: pytest.fail("Kitsu call"),
    )
    monkeypatch.setattr(
        fallback,
        "register_new_work_from_source_title",
        lambda *args, **kwargs: pytest.fail("CRC allocation"),
    )
    assert fallback.resolve_unmatched_work_with_llm(titles=[title], source_url=url) == []


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

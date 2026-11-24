import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ani_rag.models import EvidenceType, RagContentType
from ani_rag.settings import load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.structured_mcp import (
    StatementValue, StatementValuesResult, StructuredCollectionRequest, StructuredItemHit, collect_structured_items,
)
from ani_rag.wikidata_appearance import appearance_owners, archived_character_cast
from scripts.repair_wikidata_appearance_links import reconcile


def test_crosswalk_requires_unique_provider_entity_anilist_anime_and_official_owner(tmp_path):
    entries = tmp_path / "menu_work_entries.csv"
    entries.write_text("anilist_id,menu_work_id\n7,1\n8,2\n", encoding="utf-8")
    external = tmp_path / "external_id.csv"
    external.write_text(
        "entity_id,source_id,id_kind,external_id\n"
        "a,wikidata,item,Q101\na,anilist,anime,7\na,anilist,manga,8\n"
        "b,wikidata,item,Q102\nb,anilist,manga,7\n"
        "c,wikidata,item,Q103\nc,anilist,anime,8\nd,wikidata,item,Q103\nd,anilist,anime,7\n"
        "e,wikidata,item,Q104\ne,anilist,anime,7\ne,anilist,anime,8\n"
        "f,wikidata,item,Q105\nf,anilist,anime,8\n", encoding="utf-8",
    )
    assert appearance_owners(entries, external) == {"Q101": 1, "Q105": 2}
    entries.write_text("anilist_id,menu_work_id\n7,1\n8,2\n8,3\n", encoding="utf-8")
    assert appearance_owners(entries, external) == {"Q101": 1}


def test_structured_appearing_work_uses_only_statements_and_does_not_schedule_a_draft(tmp_path):
    registry = load_source_registry()
    source_id = "wikidata_character_catalog"
    source = next(s for s in registry.approved_sources if s.source_id == source_id)
    tools = dict(source.tool_bindings)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        db.executescript((Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql").read_text(encoding="utf-8"))
        adapter, work_catalog = AsyncMock(), AsyncMock()
        adapter.list_tools.return_value = set(tools.values())
        adapter.search_items.return_value = [
            StructuredItemHit(source_id, qid, "Title matching search seed", f"https://www.wikidata.org/wiki/{qid}",
                              "en", source.mcp_server, tools["search"]) for qid in ("Q1", "Q2", "Q3", "Q4")
        ]
        def statement(qid, prop="P1441", rank="normal"):
            return StatementValue(prop, {"entity_id": qid, "label": "Exact known work title"}, {"rank":rank}, ())
        cases = {
            "Q1": (statement("Q101"), statement("Q500"), statement("Q103", rank="deprecated")),
            "Q2": (statement("Q101"), statement("Q103")),
            "Q3": (statement("Q103", prop="P31"), statement("Q500")),
            "Q4": (statement("Q103"),),
        }
        adapter.get_statement_values.side_effect = lambda req: StatementValuesResult(
            source_id, source.mcp_server, tools["read"], req.item_id, cases[req.item_id],
        )
        request = StructuredCollectionRequest(source_id, RagContentType.CHARACTER, EvidenceType.REFERENCE_FACT,
                                              "seed query", "en", ("P31","P1441"), seed_menu_work_id=11)
        owners = {"Q101":11, "Q103":14}
        with patch("ani_rag.structured_mcp.appearance_owners", return_value=owners):
            stored = asyncio.run(collect_structured_items(request, registry=registry, adapter=adapter,
                                                          repository=SQLiteRagStore(db), work_catalog=work_catalog))
        assert len(stored) == 4
        work_catalog.resolve.assert_not_called()
        assert [tuple(r) for r in db.execute(
            """SELECT external_id,w.status,w.menu_work_id,w.candidate_menu_work_id,
               json_extract(d.metadata_json,'$.seed_match_status') FROM rag_work_link w JOIN rag_document d
               USING(document_id) ORDER BY external_id,w.candidate_menu_work_id"""
        )] == [("Q1","matched",11,None,"confirmed"), ("Q2","ambiguous",None,11,"unconfirmed"),
               ("Q2","ambiguous",None,14,"unconfirmed"), ("Q3","unresolved",None,None,"unconfirmed"),
               ("Q4","matched",14,None,"unconfirmed")]
        assert db.execute("SELECT COUNT(*) FROM rag_queue_job").fetchone()[0] == 0

        # Recreate an old unlinked row and verify a reversible, idempotent repair.
        db.execute("""UPDATE rag_work_link SET menu_work_id=NULL,status='unresolved',matched_on=NULL
                      WHERE document_id=(SELECT document_id FROM rag_document WHERE external_id='Q1')""")
        db.commit()
        before = reconcile(db, owners, 1, 4)
        assert [r["status"] for r in before] == ["matched","preserved","unresolved","preserved"]
        backup = tmp_path / "before.json"
        assert reconcile(db, owners, 1, 4, backup) == before
        assert backup.is_file()
        assert [r["status"] for r in reconcile(db, owners, 1, 4, backup)] == [
            "preserved","preserved","unresolved","preserved",
        ]
    finally:
        db.close()


def test_character_franchise_link_needs_same_item_and_official_cast_on_reread(tmp_path, monkeypatch):
    catalog = tmp_path / "menu_works.csv"
    catalog.write_text("menu_work_id,title_english,anilist_ids\n11,Naruto,1735\n12,Other,77\n", encoding="utf8")
    monkeypatch.setenv("ME_NU_WORK_CATALOG_DIR", str(tmp_path))
    registry = load_source_registry()
    source = next(s for s in registry.approved_sources if s.source_id == "wikidata_character_catalog")
    request = StructuredCollectionRequest(source.source_id, RagContentType.CHARACTER, EvidenceType.REFERENCE_FACT,
                                          "Naruto", "en", ("P1441",), seed_menu_work_id=11)
    tools = dict(source.tool_bindings)
    adapter = AsyncMock()
    adapter.list_tools.return_value = set(tools.values())
    adapter.search_items.return_value = [StructuredItemHit(
        source.source_id, "Q700540", "Minato Namikaze", "https://www.wikidata.org/wiki/Q700540",
        "en", source.mcp_server, tools["search"],
    )]
    adapter.get_statement_values.return_value = StatementValuesResult(
        source.source_id, source.mcp_server, tools["read"], "Q700540",
        (StatementValue("P1441", {"entity_id": "Q642", "label": "Naruto"}, {"rank": "normal"}, ()),),
    )
    db = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        db.executescript((Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql").read_text(encoding="utf8"))
        store = SQLiteRagStore(db)
        def collect():
            with patch("ani_rag.structured_mcp.appearance_owners", return_value={}):
                return asyncio.run(collect_structured_items(request, registry=registry, adapter=adapter, repository=store))[0]

        first = collect()
        original = db.execute("select raw_payload_json from rag_document where document_id=?", (first.canonical_record_id,)).fetchone()
        payload = json.loads(original[0])
        assert archived_character_cast(db, payload) is None

        def archive(doc, source, url, raw):
            db.execute("""INSERT INTO rag_source(source_id,media_name,source_kind,collect_method,site_url,source_grade,
                          copyright_status,license_basis,configuration_json,created_at,updated_at)
                          VALUES(?,?,'archive','import',?,'reference','test','test','{}','2026-01-01','2026-01-01')""",
                       (source, source, url))
            db.execute(
                """INSERT INTO rag_document(document_id,source_id,source_site_id,content_type,evidence_type,
                   collection_method,title,original_language,url,canonical_url,fetched_at,author_attribution,
                   metadata_json,raw_payload_json,content_hash,created_at) VALUES(?,?,(SELECT source_site_id FROM
                   rag_source WHERE source_id=?),'character','reference_fact',
                   'mcp_read',?,'en',?,?,'2026-01-01','unknown',?,?,?,'2026-01-01')""",
                (doc, source, source, doc, url, url, json.dumps({"dataset_version": "v1"}),
                 json.dumps({"dataset": "menu_dict", **raw}), doc),
            )
        archive("identity", "wikidata", "https://www.wikidata.org/wiki/Q700540", {"entity_kind": "character", "entity_id": 9})
        archive("cast", "anilist", "https://anilist.co/anime/1735", {"entity_kind": "entry", "field": "character", "value_entity_id": 9})
        db.execute("insert into rag_work_link(link_id,document_id,menu_work_id,status) values('wl','cast',11,'matched')")
        assert archived_character_cast(db, {**payload, "item_id": "Q123"}) is None
        assert archived_character_cast(db, {**payload, "statements": []}) is None
        for label in ("Other", "Naruto spin-off"):
            statement = {"property_id": "P1441", "value": {"entity_id": "Q643", "label": label}}
            assert archived_character_cast(db, {**payload, "statements": [statement]}) is None
            assert archived_character_cast(db, {**payload, "statements": [*payload["statements"], statement]}) is None
        db.execute("update rag_work_link set menu_work_id=12 where link_id='wl'")
        assert archived_character_cast(db, payload) is None
        db.execute("update rag_work_link set menu_work_id=11 where link_id='wl'")
        db.execute("update rag_document set metadata_json=json_set(metadata_json,'$.dataset_version','v2') where document_id='cast'")
        assert archived_character_cast(db, payload) is None
        db.execute("update rag_document set metadata_json=json_set(metadata_json,'$.dataset_version','v1') where document_id='cast'")
        db.execute("update rag_source_read_ledger set next_check_at='2020-01-01'")
        db.commit()
        assert collect().canonical_record_id == first.canonical_record_id
        row = db.execute("""select menu_work_id,matched_on,metadata_json from rag_work_link join rag_document
                            using(document_id) where document_id=?""", (first.canonical_record_id,)).fetchone()
        assert tuple(row[:2]) == (11, "wikidata_appearance_anilist_cast")
        assert json.loads(row[2])["work_match_evidence_document_ids"] == "identity,cast"
        assert json.loads(row[2])["seed_match_status"] == "confirmed"
        assert db.execute("select count(*) from rag_generation_work").fetchone()[0] == 0
    finally:
        db.close()

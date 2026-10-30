"""Reviewed catalog validation and transactional ingestion using in-memory SQLite."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import BigInteger, DefaultClause, Integer, JSON, MetaData, create_engine, event, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models import Entity, EntityExternalId, EntityTag, PreferenceTag, Topic
from app.seeds import onboarding_catalog as seed


class AsyncAdapter:
    def __init__(self, session, *, fail_second_work=False):
        self.session = session
        self.fail_second_work = fail_second_work

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def get(self, model, identity):
        return self.session.get(model, identity)

    def add(self, row):
        self.session.add(row)

    async def flush(self):
        if self.fail_second_work and any(
            isinstance(row, Entity) and "구름 정원" in row.name for row in self.session.new
        ):
            raise RuntimeError("injected second work failure")
        self.session.flush()


@pytest.fixture
def engine():
    metadata = MetaData()
    for model in (Topic, Entity, EntityExternalId, PreferenceTag, EntityTag):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
            elif isinstance(column.type, JSONB):
                column.type = JSON()
                if column.server_default is not None:
                    column.server_default = DefaultClause(text(str(column.server_default.arg).replace("::jsonb", "")))
    db_engine = create_engine("sqlite:///:memory:")

    @event.listens_for(db_engine, "connect")
    def enable_fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(db_engine)
    with Session(db_engine) as session, session.begin():
        session.add(Topic(topic_code="anime", topic_name="애니메이션", is_active=True))
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def raw_catalog():
    return json.loads(seed.DEMO_CATALOG.read_text(encoding="utf-8"))


def counts(session):
    return {model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (Entity, EntityExternalId, PreferenceTag, EntityTag)}


def settings(**changes):
    values = {"database_url": "postgresql+psycopg://demo:demo@127.0.0.1/menu_catalog_test", "enable_dev_api": True}
    values.update(changes)
    return SimpleNamespace(**values)


def test_example_is_explicitly_synthetic_and_has_korean_labels():
    document = seed.load_catalog(seed.DEMO_CATALOG)
    assert document.demo is True
    assert len(document.works) == 2
    assert all(work.provider == "demo" and "가상" in work.name for work in document.works)
    assert {tag.kind for tag in document.tags} == {"genre", "theme"}


@pytest.mark.parametrize("case", [
    "duplicate_work", "duplicate_tag", "duplicate_work_tag", "unknown_tag", "unknown_field",
    "blank_alias", "duplicate_alias", "non_korean_name", "non_korean_tag", "bad_image",
    "credential_image", "numeric_external_id", "boolean_year", "demo_without_marker",
])
def test_invalid_documents_fail_before_ingestion(raw_catalog, case):
    data = deepcopy(raw_catalog)
    if case == "duplicate_work":
        data["works"].append(deepcopy(data["works"][0]))
    elif case == "duplicate_tag":
        data["tags"].append(deepcopy(data["tags"][0]))
    elif case == "duplicate_work_tag":
        data["works"][0]["tags"].append(deepcopy(data["works"][0]["tags"][0]))
    elif case == "unknown_tag":
        data["works"][0]["tags"][0]["code"] = "missing"
    elif case == "unknown_field":
        data["works"][0]["entityId"] = 100
    elif case == "blank_alias":
        data["works"][0]["aliases"] = ["   "]
    elif case == "duplicate_alias":
        data["works"][0]["aliases"] = ["별명", " 별명 "]
    elif case == "non_korean_name":
        data["works"][0]["name"] = "[DEMO] English-only title"
    elif case == "non_korean_tag":
        data["tags"][0]["displayName"] = "Fantasy"
    elif case == "bad_image":
        data["works"][0]["imageUrl"] = "javascript:alert(1)"
    elif case == "credential_image":
        data["works"][0]["imageUrl"] = "https://user:secret@example.com/image.png"
    elif case == "numeric_external_id":
        data["works"][0]["externalId"] = 123
    elif case == "boolean_year":
        data["works"][0]["releaseYear"] = True
    elif case == "demo_without_marker":
        data["demo"] = False
    with pytest.raises(ValidationError):
        seed.CatalogDocument.model_validate(data)


@pytest.mark.parametrize("changes, expected_name, demo", [
    ({}, "wrong_database", True),
    ({}, "", True),
    ({}, "menu_catalog_test", False),
    ({"database_url": "postgresql+psycopg://demo:demo@database.example/menu_catalog_test"}, "menu_catalog_test", True),
    ({"database_url": "postgresql+psycopg://demo:demo@127.0.0.1/menu_catalog_test?host=database.example"}, "menu_catalog_test", True),
    ({"enable_dev_api": False}, "menu_catalog_test", True),
    ({"database_url": "sqlite:///menu_catalog_test"}, "menu_catalog_test", True),
])
def test_demo_requires_explicit_name_flag_and_local_dev_target(changes, expected_name, demo):
    with pytest.raises(seed.CatalogError):
        seed.validate_target(settings(**changes), expected_database=expected_name,
                             document=seed.load_catalog(seed.DEMO_CATALOG), demo=demo)


def test_reviewed_catalog_can_target_explicit_remote_database(raw_catalog):
    data = deepcopy(raw_catalog)
    data["demo"] = False
    for work in data["works"]:
        work["provider"] = "reviewed"
    seed.validate_target(settings(database_url="postgresql+psycopg://demo:demo@reviewed.example/reviewed_catalog"),
                         expected_database="reviewed_catalog", document=seed.CatalogDocument.model_validate(data), demo=False)


@pytest.mark.asyncio
async def test_preview_makes_no_writes_and_reports_plan(engine):
    with Session(engine) as session:
        before = counts(session)
        result = await seed.seed_catalog(AsyncAdapter(session), seed.load_catalog(seed.DEMO_CATALOG))
        assert result["mode"] == "dry_run"
        assert (result["new_works"], result["new_tags"], result["added_links"]) == (2, 4, 4)
        assert all(work["entity_id"] is None for work in result["works"])
        assert counts(session) == before
        assert not session.new and not session.dirty and not session.deleted


@pytest.mark.asyncio
async def test_repeat_import_preserves_ids_and_creates_no_duplicates(engine):
    document = seed.load_catalog(seed.DEMO_CATALOG)
    with Session(engine) as session, session.begin():
        first = await seed.seed_catalog(AsyncAdapter(session), document, apply=True)
    with Session(engine) as session, session.begin():
        second = await seed.seed_catalog(AsyncAdapter(session), document, apply=True)
        assert second["works"] == first["works"]
        assert all(second[key] == 0 for key in ("new_works", "updated_works", "new_tags", "updated_tags", "added_links", "removed_links"))
        assert counts(session) == {"entity": 2, "entity_external_id": 2, "preference_tag": 4, "entity_tag": 4}


@pytest.mark.asyncio
async def test_upsert_uses_external_identity_and_replaces_only_included_work_tags(engine, raw_catalog):
    with Session(engine) as session, session.begin():
        initial = await seed.seed_catalog(AsyncAdapter(session), seed.CatalogDocument.model_validate(raw_catalog), apply=True)
    changed = deepcopy(raw_catalog)
    changed["works"] = [changed["works"][0]]
    changed["works"][0]["name"] = "[DEMO] 가상 별빛 우체국 수정 제목"
    changed["works"][0]["aliases"] = ["새 가상 별명"]
    changed["works"][0]["tags"] = [{"kind": "theme", "code": "demo_cloud_garden"}]
    changed["tags"][0]["displayName"] = "가상 판타지 수정 표시명"
    document = seed.CatalogDocument.model_validate(changed)
    with Session(engine) as session:
        preview = await seed.seed_catalog(AsyncAdapter(session), document)
        assert (preview["updated_works"], preview["updated_tags"], preview["added_links"], preview["removed_links"]) == (1, 1, 1, 2)
        assert session.get(Entity, initial["works"][0]["entity_id"]).name == raw_catalog["works"][0]["name"]
    with Session(engine) as session, session.begin():
        result = await seed.seed_catalog(AsyncAdapter(session), document, apply=True)
        assert result["works"][0]["entity_id"] == initial["works"][0]["entity_id"]
        assert session.scalar(select(func.count()).select_from(EntityTag).where(EntityTag.entity_id == initial["works"][1]["entity_id"])) == 2
        assert session.scalar(select(func.count()).select_from(PreferenceTag)) == 4
    with Session(engine) as session:
        repeat = await seed.seed_catalog(AsyncAdapter(session), document)
        assert repeat["updated_works"] == repeat["updated_tags"] == repeat["added_links"] == repeat["removed_links"] == 0


@pytest.mark.asyncio
async def test_equal_titles_do_not_merge_different_external_identities(engine, raw_catalog):
    data = deepcopy(raw_catalog)
    data["works"][1]["name"] = data["works"][0]["name"]
    with Session(engine) as session, session.begin():
        result = await seed.seed_catalog(AsyncAdapter(session), seed.CatalogDocument.model_validate(data), apply=True)
        assert len({work["entity_id"] for work in result["works"]}) == 2


@pytest.mark.asyncio
async def test_missing_topic_is_not_created(engine, raw_catalog):
    data = deepcopy(raw_catalog)
    data["topicCode"] = "unknown"
    with Session(engine) as session:
        with pytest.raises(seed.CatalogError, match="existing active topic"):
            await seed.seed_catalog(AsyncAdapter(session), seed.CatalogDocument.model_validate(data), apply=True)
        assert session.scalar(select(func.count()).select_from(Topic)) == 1
        assert all(value == 0 for value in counts(session).values())


@pytest.mark.asyncio
async def test_cross_topic_external_identity_conflict_is_rejected(engine, raw_catalog):
    with Session(engine) as session, session.begin():
        await seed.seed_catalog(AsyncAdapter(session), seed.CatalogDocument.model_validate(raw_catalog), apply=True)
        session.add(Topic(topic_code="other", topic_name="다른 분야", is_active=True))
    data = deepcopy(raw_catalog)
    data["topicCode"] = "other"
    with Session(engine) as session, session.begin():
        before = counts(session)
        with pytest.raises(seed.CatalogError, match="another topic"):
            await seed.seed_catalog(AsyncAdapter(session), seed.CatalogDocument.model_validate(data), apply=True)
        assert counts(session) == before


@pytest.mark.asyncio
async def test_second_work_failure_rolls_back_whole_catalog(engine):
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match="second work"):
            with session.begin():
                await seed.seed_catalog(AsyncAdapter(session, fail_second_work=True), seed.load_catalog(seed.DEMO_CATALOG), apply=True)
    with Session(engine) as session:
        assert all(value == 0 for value in counts(session).values())
        assert session.scalar(select(func.count()).select_from(Topic)) == 1


@pytest.mark.asyncio
async def test_duplicate_resolved_entity_from_multiple_providers_is_rejected(engine, raw_catalog):
    data = deepcopy(raw_catalog)
    data["demo"] = False
    data["works"] = [data["works"][0]]
    data["works"][0]["provider"] = "reviewed"
    with Session(engine) as session, session.begin():
        result = await seed.seed_catalog(AsyncAdapter(session), seed.CatalogDocument.model_validate(data), apply=True)
        session.add(EntityExternalId(entity_id=result["works"][0]["entity_id"], provider="mirror", external_id="123"))
    second = deepcopy(data["works"][0])
    second.update(provider="mirror", externalId="123")
    data["works"].append(second)
    with Session(engine) as session:
        before = counts(session)
        with pytest.raises(seed.CatalogError, match="canonical external identity"):
            await seed.seed_catalog(AsyncAdapter(session), seed.CatalogDocument.model_validate(data), apply=True)
        assert counts(session) == before


@pytest.mark.asyncio
async def test_explicit_demo_url_is_validated_before_creating_engine(monkeypatch):
    def forbidden_engine(*args, **kwargs):
        pytest.fail("invalid target must not create an engine")
    monkeypatch.setattr(seed, "create_async_engine", forbidden_engine)
    with pytest.raises(seed.CatalogError, match="ENABLE_DEV_API"):
        await seed.run(document=seed.load_catalog(seed.DEMO_CATALOG), expected_database="menu_catalog_test",
                       demo=True, apply=False,
                       database_url="postgresql+psycopg://test:test@127.0.0.1/menu_catalog_test")


@pytest.mark.asyncio
async def test_ambiguous_target_configuration_is_rejected():
    with pytest.raises(seed.CatalogError, match="not both"):
        await seed.run(document=seed.load_catalog(seed.DEMO_CATALOG), expected_database="menu_catalog_test",
                       demo=True, apply=False, settings=settings(),
                       database_url="postgresql+psycopg://test:test@127.0.0.1/menu_catalog_test")


@pytest.mark.asyncio
async def test_apply_locks_entities_then_tags_in_id_order_before_writes(engine, raw_catalog):
    with Session(engine) as session, session.begin():
        await seed.seed_catalog(AsyncAdapter(session), seed.CatalogDocument.model_validate(raw_catalog), apply=True)
    changed = deepcopy(raw_catalog)
    changed["works"].reverse()
    changed["tags"].reverse()
    changed["works"][0]["name"] += " 수정"
    changed["tags"][0]["displayName"] += " 수정"
    document = seed.CatalogDocument.model_validate(changed)

    class LockRecorder(AsyncAdapter):
        def __init__(self, session):
            super().__init__(session)
            self.lock_sql = []

        async def scalars(self, statement):
            if getattr(statement, "_for_update_arg", None) is not None:
                assert not self.session.new and not self.session.dirty and not self.session.deleted
                self.lock_sql.append(" ".join(str(statement.compile(
                    dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
                )).split()))
            return await super().scalars(statement)

        async def flush(self):
            assert len(self.lock_sql) == 2
            await super().flush()

    with Session(engine) as session, session.begin():
        adapter = LockRecorder(session)
        await seed.seed_catalog(adapter, document, apply=True)
        assert len(adapter.lock_sql) == 2
        assert "WHERE entity.entity_id IN (1, 2) ORDER BY entity.entity_id FOR UPDATE" in adapter.lock_sql[0]
        assert "WHERE preference_tag.tag_id IN (1, 2, 3, 4) ORDER BY preference_tag.tag_id FOR UPDATE" in adapter.lock_sql[1]
    with Session(engine) as session:
        preview = LockRecorder(session)
        await seed.seed_catalog(preview, document, apply=False)
        assert preview.lock_sql == []
        assert not session.new and not session.dirty and not session.deleted


def test_file_validation_does_not_echo_bad_image_credentials(tmp_path, raw_catalog):
    raw_catalog["works"][0]["imageUrl"] = "https://user:secret@example.com/image.png"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(raw_catalog), encoding="utf-8")
    with pytest.raises(seed.CatalogError) as error:
        seed.load_catalog(path)
    assert "works.0.imageUrl" in str(error.value)
    assert "secret" not in str(error.value)

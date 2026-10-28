"""Work catalog CSV loading, idempotent entity upsert and carousel work links (in-memory SQLite)."""

import pytest
from sqlalchemy import BigInteger, Column, Integer, JSON, MetaData, Table, create_engine, event, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.models import (
    Content, ContentEntityLink, Draft, Entity, EntityExternalId, EntityTag, PreferenceTag, Topic,
)
from app.repositories.carousel_repository import CAROUSEL_METHOD
from app.repositories.content_work_repository import link_carousel_works
from app.seeds import menu_works as seed


MODELS = (Topic, Entity, EntityExternalId, PreferenceTag, EntityTag, Draft, Content, ContentEntityLink)
WORKS_HEADER = ("menu_work_id,title_ko,title_ko_source,title_ko_check,title_romaji,title_english,"
                "title_native,first_year,last_year,n_entries,popularity,anilist_ids")
ENTRIES_HEADER = "anilist_id,mal_id,menu_work_id,format,year,title_ko,title_romaji,title_english,title_native,popularity"


class AsyncAdapter:
    def __init__(self, session):
        self.session = session

    def get_bind(self):
        return self.session.get_bind()

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def execute(self, statement):
        return self.session.execute(statement)

    def add(self, row):
        self.session.add(row)

    async def flush(self):
        self.session.flush()


@pytest.fixture
def engine():
    metadata = MetaData()
    for model in MODELS:
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
            elif isinstance(column.type, JSONB):
                # PostgreSQL '::jsonb' defaults are not SQLite syntax; fixtures pass values.
                column.type, column.server_default = JSON(), None
    references = {fk.target_fullname for table in list(metadata.tables.values()) for fk in table.foreign_keys}
    for reference in references:
        table_name, column_name = reference.split(".")
        if table_name not in metadata.tables:
            Table(table_name, metadata, Column(column_name, Integer, primary_key=True))
    db_engine = create_engine("sqlite:///:memory:")

    @event.listens_for(db_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(db_engine)
    with Session(db_engine) as session, session.begin():
        session.add_all([
            Topic(topic_id=1, topic_code="anime", topic_name="애니메이션", is_active=True),
            Topic(topic_id=2, topic_code="movie", topic_name="영화", is_active=True),
        ])
    yield db_engine
    db_engine.dispose()


def write_csv(path, header, rows):
    # Excel writes a byte order mark; the loader must accept it.
    path.write_text("﻿" + "\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def work_row(number, korean="", check="", romaji="", english="", native="", year=""):
    return ",".join([str(number), korean, "", check, romaji, english, native, str(year), "", "1", "1", ""])


@pytest.fixture
def catalog(tmp_path):
    works = write_csv(tmp_path / "menu_works.csv", WORKS_HEADER, [
        work_row(1, "진격의 거인", "", "Shingeki no Kyojin", "Attack on Titan", "進撃の巨人", 2013),
        work_row(14, "너의 이름은.", "확인 필요", "Kimi no Na wa.", "Your Name.", "君の名は。", 2016),
        work_row(2000, "", "입력 필요", "Kaitou Joker", "Mysterious Joker", "怪盗ジョーカー", 2014),
    ])
    entries = write_csv(tmp_path / "menu_work_entries.csv", ENTRIES_HEADER, [
        "16498,16498,1,TV,2013,,,,,1",
        "99147,35760,1,MOVIE,2018,,,,,1",
        "21519,32281,14,MOVIE,2016,,,,,1",
        "123,456,2000,TV,2014,,,,,1",
    ])
    return works, entries


def test_load_works_names_aliases_formats_and_search_visibility(catalog):
    titan, your_name, joker = seed.load_works(*catalog)

    assert titan == seed.MenuWork(
        1, "진격의 거인", ("진격의거인", "Shingeki no Kyojin", "Attack on Titan", "進撃の巨人"), 2013, "SERIES", True,
    )
    # A Korean title still marked "확인 필요" is searchable; a movie-only work is a MOVIE.
    assert (your_name.name, your_name.is_active, your_name.media_format) == ("너의 이름은.", True, "MOVIE")
    assert your_name.aliases[0] == "너의이름은."
    # Without a Korean title the work stays linkable but hidden from onboarding search.
    assert (joker.name, joker.aliases, joker.is_active) == ("Mysterious Joker", ("Kaitou Joker", "怪盗ジョーカー"), False)


@pytest.mark.parametrize("rows", [
    [work_row(1, "가"), work_row(1, "나")],   # duplicate number
    [work_row("abc", "가")],                  # not a number
    [work_row(0, "가")],                      # not positive
    [work_row(5)],                            # no title at all
])
def test_load_works_rejects_bad_numbers_and_missing_titles(tmp_path, rows):
    works = write_csv(tmp_path / "menu_works.csv", WORKS_HEADER, rows)
    entries = write_csv(tmp_path / "menu_work_entries.csv", ENTRIES_HEADER, [])
    with pytest.raises(seed.MenuWorksError):
        seed.load_works(works, entries)


def add_entity(session, number, *, topic_id=1, name="옛 이름", provider="menu_work"):
    entity = Entity(topic_id=topic_id, entity_type="anime", name=name, aliases=[], is_active=True)
    session.add(entity)
    session.flush()
    session.add(EntityExternalId(provider=provider, external_id=str(number), entity_id=entity.entity_id))
    session.flush()
    return entity


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


@pytest.mark.asyncio
async def test_seed_previews_then_upserts_by_number_and_keeps_tags(engine, catalog):
    works = seed.load_works(*catalog)
    with Session(engine) as session, session.begin():
        starter = add_entity(session, 1, name="진격의 거인 (starter)")
        add_entity(session, "fixture-1", name="[DEMO] 가상 작품", provider="demo")
        tag = PreferenceTag(topic_id=1, kind="genre", code="action", display_name="액션")
        session.add(tag)
        session.flush()
        session.add(EntityTag(entity_id=starter.entity_id, tag_id=tag.tag_id, topic_id=1))
        starter_id, tag_id = starter.entity_id, tag.tag_id

    with Session(engine) as session, session.begin():
        preview = await seed.seed_works(AsyncAdapter(session), works)
        assert (count(session, Entity), count(session, EntityExternalId)) == (2, 2)
    assert {key: preview[key] for key in ("mode", "created", "updated", "unchanged", "active", "inactive")} == {
        "mode": "dry_run", "created": 2, "updated": 1, "unchanged": 0, "active": 2, "inactive": 1,
    }

    with Session(engine) as session, session.begin():
        applied = await seed.seed_works(AsyncAdapter(session), works, apply=True)
    assert (applied["created"], applied["updated"], applied["kept_not_in_csv"]) == (2, 1, 0)
    with Session(engine) as session:
        by_number = dict(session.execute(
            select(EntityExternalId.external_id, Entity)
            .join(Entity, Entity.entity_id == EntityExternalId.entity_id)
            .where(EntityExternalId.provider == "menu_work")
        ).all())
        assert by_number["1"].entity_id == starter_id and by_number["1"].name == "진격의 거인"
        assert session.scalars(select(EntityTag.tag_id).where(EntityTag.entity_id == starter_id)).all() == [tag_id]
        assert (by_number["14"].media_format, by_number["2000"].is_active) == ("MOVIE", False)
        assert session.scalar(select(Entity.name).join(EntityExternalId).where(EntityExternalId.provider == "demo")) == "[DEMO] 가상 작품"

    with Session(engine) as session, session.begin():
        again = await seed.seed_works(AsyncAdapter(session), works, apply=True)
    assert (again["created"], again["updated"], again["unchanged"]) == (0, 0, 3)


@pytest.mark.asyncio
async def test_seed_refuses_a_number_owned_by_another_topic(engine, catalog):
    with Session(engine) as session, session.begin():
        add_entity(session, 14, topic_id=2)
    with Session(engine) as session, session.begin(), pytest.raises(seed.MenuWorksError):
        await seed.seed_works(AsyncAdapter(session), seed.load_works(*catalog), apply=True)


def add_content(session, menu_work_ids, *, carousel=True):
    # A draft needs its own producer run (ck_draft_owner, one draft per run).
    run_id = session.execute(text("INSERT INTO agent_run DEFAULT VALUES")).lastrowid
    draft = Draft(agent_run_id=run_id, topic_id=1, production_type="ai", content_type="news", title="카드", body="본문",
                  structure_template={"method": CAROUSEL_METHOD, "menu_work_ids": menu_work_ids},
                  status="published")
    session.add(draft)
    session.flush()
    content = Content(source_draft_id=draft.draft_id, topic_id=1, production_type="ai", title="카드",
                      status="active", judgment_status="confirmed",
                      ai_judgment_basis={"method": CAROUSEL_METHOD} if carousel else None)
    session.add(content)
    session.flush()
    return content.content_id


def links(session):
    return session.execute(
        select(ContentEntityLink.content_id, EntityExternalId.external_id, ContentEntityLink.sort_order,
               ContentEntityLink.link_method)
        .join(EntityExternalId, EntityExternalId.entity_id == ContentEntityLink.entity_id)
        .order_by(ContentEntityLink.content_id, ContentEntityLink.sort_order)
    ).all()


@pytest.mark.asyncio
async def test_carousel_links_keep_package_order_and_report_unknown_works(engine):
    with Session(engine) as session, session.begin():
        for number in (1, 2):
            add_entity(session, number)
        add_entity(session, 3, topic_id=2)
        main = add_content(session, [1, 999, 2])
        other_topic = add_content(session, [3])
        add_content(session, [1], carousel=False)   # not a carousel: left alone
        add_content(session, [])

    with Session(engine) as session, session.begin():
        first = await link_carousel_works(AsyncAdapter(session))
        again = await link_carousel_works(AsyncAdapter(session))
        assert links(session) == [(main, "1", 1, "carousel"), (main, "2", 3, "carousel")]
    assert first == {"links_added": 2, "contents_linked": 1, "order_conflicts": 0, "unresolved_menu_work_ids": [3, 999]}
    assert again["links_added"] == 0 and again["unresolved_menu_work_ids"] == [3, 999]
    assert other_topic not in {row[0] for row in links(Session(engine))}

    with Session(engine) as session, session.begin():
        add_entity(session, 999)                    # the catalog catches up later
        later = await link_carousel_works(AsyncAdapter(session))
        assert links(session)[1] == (main, "999", 2, "carousel")
    assert later["links_added"] == 1 and later["unresolved_menu_work_ids"] == [3]


CHIPS_HEADER = "kind,code,anilist_name,category,works_pct,display_name,name_check,show,sort_order"


@pytest.fixture
def chip_files(tmp_path):
    chips = write_csv(tmp_path / "chips.csv", CHIPS_HEADER, [
        "genre,comedy,Comedy,Genre,48.4,코미디,,main,1",
        "genre,action,Action,Genre,40.0,액션,,main,2",
        "genre,sci_fi,Sci-Fi,Genre,10.0,SF,,main,3",
        "theme,school,School,Setting-Scene,30.1,학원,,main,1",
        "genre,ecchi,Ecchi,Genre,13.0,,입력 필요,off,100",
    ])
    links = write_csv(tmp_path / "work_chips.csv", "menu_work_id,kind,code", [
        "1,genre,comedy", "1,genre,action", "1,theme,school", "1,genre,ecchi",
        "14,theme,school", "2000,genre,action",
    ])
    return chips, links


@pytest.mark.parametrize("row", [
    "genre,horror,Horror,Genre,1.0,,,main,3",           # a shown chip needs a screen name
    "genre,horror,Horror,Genre,1.0,호러,입력 필요,main,3",  # and its name must be reviewed
    "genre,Horror,Horror,Genre,1.0,호러,,main,3",        # codes are lowercase
    "genre,horror,Horror,Genre,1.0,호러,,maybe,3",       # unknown show value
    "genre,comedy,Comedy,Genre,1.0,코미디,,main,3",      # duplicate chip
])
def test_load_chips_rejects_unreviewed_or_malformed_rows(tmp_path, chip_files, row):
    chips, _ = chip_files
    rows = chips.read_text(encoding="utf-8-sig").strip().splitlines()[1:] + [row]
    with pytest.raises(seed.MenuWorksError):
        seed.load_chips(write_csv(tmp_path / "bad_chips.csv", CHIPS_HEADER, rows))


@pytest.mark.asyncio
async def test_seed_chips_shows_catalog_chips_turns_off_others_and_replaces_work_links(engine, catalog, chip_files):
    chips = seed.load_chips(chip_files[0])
    work_chips = seed.load_work_chips(chip_files[1], chips)
    with Session(engine) as session, session.begin():
        starter = add_entity(session, 1)
        demo = add_entity(session, "fixture-1", name="[DEMO] 가상 작품", provider="demo")
        old = {code: PreferenceTag(topic_id=1, kind=kind, code=code, display_name=label)
               for kind, code, label in [("genre", "action", "액션(starter)"), ("theme", "letters", "편지")]}
        session.add_all(old.values())
        session.flush()
        session.add_all([EntityTag(entity_id=starter.entity_id, tag_id=old["letters"].tag_id, topic_id=1),
                         EntityTag(entity_id=demo.entity_id, tag_id=old["letters"].tag_id, topic_id=1)])
        demo_id = demo.entity_id

    with Session(engine) as session, session.begin():
        await seed.seed_works(AsyncAdapter(session), seed.load_works(*catalog), apply=True)
        report = await seed.seed_chips(AsyncAdapter(session), chips, work_chips, apply=True)
    assert {key: report[key] for key in ("chips_shown", "chips_created", "chips_updated", "chips_deactivated")} == {
        "chips_shown": 4, "chips_created": 3, "chips_updated": 1, "chips_deactivated": ["theme:letters"],
    }

    with Session(engine) as session:
        tags = {tag.code: tag for tag in session.scalars(select(PreferenceTag))}
        assert (tags["action"].display_name, tags["action"].is_active, tags["letters"].is_active) == ("액션", True, False)
        linked = session.execute(
            select(EntityExternalId.external_id, PreferenceTag.code)
            .join(EntityTag, EntityTag.entity_id == EntityExternalId.entity_id)
            .join(PreferenceTag, PreferenceTag.tag_id == EntityTag.tag_id)
        ).all()
        by_work = {}
        for number, code in linked:
            by_work.setdefault(number, set()).add(code)
        # Off chips (ecchi) are never linked; the demo work keeps its own link.
        assert by_work == {"1": {"comedy", "action", "school"}, "14": {"school"}, "2000": {"action"},
                           "fixture-1": {"letters"}}
        assert session.scalar(select(func.count()).select_from(EntityTag).where(EntityTag.entity_id == demo_id)) == 1

    with Session(engine) as session, session.begin():
        again = await seed.seed_chips(AsyncAdapter(session), chips, work_chips, apply=True)
    assert (again["chips_created"], again["chips_updated"], again["chips_deactivated"],
            again["links_added"], again["links_removed"]) == (0, 0, [], 0, 0)

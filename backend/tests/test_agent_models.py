from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db.base import Base
from app.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStep,
    Content,
    ContentSource,
    CreatorChannel,
    Draft,
    JudgmentLog,
    SourceSite,
    Subtopic,
    Topic,
    TopicCluster,
    UserAccount,
)


EXPECTED_COLUMNS = {
    "user_account": 15,
    "topic": 7,
    "subtopic": 7,
    "source_site": 12,
    "creator_channel": 12,
    "topic_cluster": 9,
    "agent_run": 15,
    "agent_run_step": 12,
    "agent_run_source": 9,
    "draft": 16,
    "judgment_log": 14,
    "content": 25,
    "content_source": 9,
}


def test_movie_agent_phase_one_tables_are_registered():
    assert EXPECTED_COLUMNS.keys() <= Base.metadata.tables.keys()


def test_movie_agent_phase_one_column_counts_match_schema():
    actual = {
        table_name: len(Base.metadata.tables[table_name].columns)
        for table_name in EXPECTED_COLUMNS
    }

    assert actual == EXPECTED_COLUMNS


def test_all_phase_one_foreign_keys_resolve():
    for table_name in EXPECTED_COLUMNS:
        table = Base.metadata.tables[table_name]
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.name in EXPECTED_COLUMNS


def test_models_compile_for_postgresql():
    dialect = postgresql.dialect()

    for table_name in EXPECTED_COLUMNS:
        statement = str(
            CreateTable(Base.metadata.tables[table_name]).compile(
                dialect=dialect
            )
        )
        assert f"CREATE TABLE {table_name}" in statement

    assert "VECTOR" in str(
        CreateTable(Content.__table__).compile(dialect=dialect)
    )


def test_expected_model_classes_use_their_physical_table_names():
    assert UserAccount.__tablename__ == "user_account"
    assert Topic.__tablename__ == "topic"
    assert Subtopic.__tablename__ == "subtopic"
    assert SourceSite.__tablename__ == "source_site"
    assert CreatorChannel.__tablename__ == "creator_channel"
    assert TopicCluster.__tablename__ == "topic_cluster"
    assert AgentRun.__tablename__ == "agent_run"
    assert AgentRunStep.__tablename__ == "agent_run_step"
    assert AgentRunSource.__tablename__ == "agent_run_source"
    assert Draft.__tablename__ == "draft"
    assert JudgmentLog.__tablename__ == "judgment_log"
    assert Content.__tablename__ == "content"
    assert ContentSource.__tablename__ == "content_source"

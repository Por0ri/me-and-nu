"""The reply migration and ORM agree on retention and answer constraints."""

from datetime import datetime, timezone

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

from app.models import InquiryAnswer


@pytest.mark.parametrize("source", ["migration", "model"])
def test_answer_is_unique_bounded_and_removed_with_inquiry(source, monkeypatch):
    with sa.create_engine("sqlite://").begin() as db:
        db.exec_driver_sql("PRAGMA foreign_keys=ON")
        db.exec_driver_sql("CREATE TABLE inquiry (inquiry_id BIGINT PRIMARY KEY)")
        db.exec_driver_sql("CREATE TABLE admin_account (admin_account_id BIGINT PRIMARY KEY)")
        db.exec_driver_sql("INSERT INTO inquiry VALUES (1), (2)")
        db.exec_driver_sql("INSERT INTO admin_account VALUES (7)")
        revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("83a2f9c7d104").module
        monkeypatch.setattr(revision, "op", Operations(MigrationContext.configure(db)))
        if source == "migration":
            revision.upgrade()
        else:
            InquiryAnswer.__table__.create(db)
        table = InquiryAnswer.__table__
        value = dict(inquiry_id=1, body="실제 답변", admin_account_id=7, answered_at=datetime.now(timezone.utc))
        db.execute(table.insert().values(**value))
        with pytest.raises(sa.exc.IntegrityError):
            db.execute(table.insert().values(**value))
        for body in ("", "a" * 5001):
            with pytest.raises(sa.exc.IntegrityError):
                db.execute(table.insert().values(**(value | {"inquiry_id": 2, "body": body})))
        with pytest.raises(RuntimeError, match="Refusing to discard published inquiry answers"):
            revision.downgrade()
        assert db.scalar(sa.select(sa.func.count()).select_from(table)) == 1
        db.exec_driver_sql("DELETE FROM inquiry WHERE inquiry_id=1")
        assert db.scalar(sa.select(sa.func.count()).select_from(table)) == 0
        revision.downgrade()
        assert "inquiry_answer" not in sa.inspect(db).get_table_names()

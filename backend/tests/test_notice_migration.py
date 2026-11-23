"""Existing notifications survive upgrade; saved notices cannot be discarded."""

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa


@pytest.fixture
def migrated():
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("CREATE TABLE admin_account(admin_account_id BIGINT PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO admin_account VALUES (1)")
        connection.exec_driver_sql("CREATE TABLE notification(notification_id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, notification_type VARCHAR(30) NOT NULL, title VARCHAR(100), body VARCHAR(500))")
        connection.exec_driver_sql("INSERT INTO notification VALUES (1, 7, 'system_notice', 'existing', 'keep')")
        revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("b7d4e2a9c601").module
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.upgrade()
            yield connection, revision
    engine.dispose()


def insert_notice(db, *, status="draft", scheduled=None, published=None):
    db.execute(sa.text("INSERT INTO notice(notice_id,title,body,status,scheduled_at,published_at,created_by_admin_id,updated_by_admin_id) VALUES(1,'notice','body',:status,:scheduled,:published,1,1)"),
               {"status": status, "scheduled": scheduled, "published": published})


def test_upgrade_keeps_existing_notifications_and_empty_downgrade(migrated):
    db, revision = migrated
    assert db.execute(sa.text("SELECT title,body,notice_id FROM notification")).one() == ("existing", "keep", None)
    revision.downgrade()
    assert db.execute(sa.text("SELECT title,body FROM notification")).one() == ("existing", "keep")
    assert not sa.inspect(db).has_table("notice")


def test_saved_notices_block_downgrade(migrated):
    db, revision = migrated
    insert_notice(db)
    with pytest.raises(RuntimeError, match="Refusing to discard"):
        revision.downgrade()
    assert db.scalar(sa.text("SELECT count(*) FROM notice")) == 1
    assert "notice_id" in {column["name"] for column in sa.inspect(db).get_columns("notification")}


@pytest.mark.parametrize("status,scheduled,published", [
    ("scheduled", None, None),
    ("draft", "2026-11-23 03:00:00", None),
    ("published", None, None),
    ("unknown", None, None),
    ("published", "2026-11-23 03:00:00", "2026-11-23 02:00:00"),
])
def test_invalid_publication_state_rejected(migrated, status, scheduled, published):
    with pytest.raises(sa.exc.IntegrityError):
        insert_notice(migrated[0], status=status, scheduled=scheduled, published=published)


def test_unique_notice_delivery_and_type_constraint(migrated):
    db, _ = migrated
    insert_notice(db, status="published", published="2026-11-23 03:00:00")
    db.exec_driver_sql("INSERT INTO notification VALUES(2,7,'system_notice','test','preview',1)")
    with pytest.raises(sa.exc.IntegrityError):
        db.exec_driver_sql("INSERT INTO notification VALUES(3,7,'system_notice','test','preview',1)")
    with pytest.raises(sa.exc.IntegrityError):
        db.exec_driver_sql("INSERT INTO notification VALUES(4,8,'recommended_post','test','preview',1)")
    # Legacy unlinked notifications remain valid even for the same recipient.
    db.exec_driver_sql("INSERT INTO notification VALUES(5,7,'system_notice','another','legacy',NULL)")

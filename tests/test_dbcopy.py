"""The SQLite to PostgreSQL copy: exact, refuses to overwrite, all-or-nothing, and keeps ids usable afterwards."""
import os

import pytest
from sqlalchemy import create_engine, text

from academy import create_app, dbcopy
from academy.config import TestConfig
from academy.dbcopy import CopyError, copy_database
from academy.models import Course, Enrollment, Payment, User, db
from academy.seed import seed

PG = os.environ.get("TEST_DATABASE_URL", "")
needs_pg = pytest.mark.skipif(not PG.startswith("postgres"), reason="set TEST_DATABASE_URL to a PostgreSQL URL")


def make_app(url, tmp_path, name):
    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = url
        UPLOAD_DIR = str(tmp_path / f"up-{name}")

    return create_app(Cfg)  # in testing mode this creates all tables


@pytest.fixture
def source(tmp_path):
    url = f"sqlite:///{tmp_path / 'source.db'}"
    app = make_app(url, tmp_path, "src")
    with app.app_context():
        seed()
        student = User(email="hana@example.com", full_name="Hana Tesfaye", phone="+251911000000", city="Addis Ababa")
        student.set_password("secret123")
        db.session.add(student)
        db.session.commit()
        course = Course.query.first()
        db.session.add(Payment(user_id=student.id, course_id=course.id, amount="2500.50", currency="ETB", status="paid",
                               method="manual", channel="bank"))
        db.session.add(Enrollment(user_id=student.id, course_id=course.id))
        db.session.commit()
    return app, url


def empty_sqlite_target(tmp_path):
    url = f"sqlite:///{tmp_path / 'target.db'}"
    return make_app(url, tmp_path, "dst"), url


def counts(url):
    engine = create_engine(url)
    with engine.connect() as conn:
        names = [t.name for t in db.metadata.sorted_tables]
        result = {n: conn.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar() for n in names}
    engine.dispose()
    return result


def test_copy_is_exact_and_verified(source, tmp_path):
    _, src_url = source
    _, dst_url = empty_sqlite_target(tmp_path)
    report = copy_database(src_url, dst_url, check_version=False)
    assert report.ok and report.total_rows > 10
    assert counts(src_url) == counts(dst_url)
    assert all(t.source_digest == t.target_digest and t.source_digest for t in report.tables)
    engine = create_engine(dst_url)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT email, full_name FROM \"user\" WHERE email = 'hana@example.com'")).one()
    engine.dispose()
    assert tuple(row) == ("hana@example.com", "Hana Tesfaye")


def test_dry_run_copies_nothing(source, tmp_path):
    _, src_url = source
    _, dst_url = empty_sqlite_target(tmp_path)
    report = copy_database(src_url, dst_url, dry_run=True, check_version=False)
    assert report.dry_run and report.total_rows > 10
    assert sum(counts(dst_url).values()) == 0


def test_refuses_a_target_that_already_has_data(source, tmp_path):
    _, src_url = source
    _, dst_url = empty_sqlite_target(tmp_path)
    copy_database(src_url, dst_url, check_version=False)
    with pytest.raises(CopyError, match="not empty"):
        copy_database(src_url, dst_url, check_version=False)


def test_refuses_a_target_without_the_schema(source, tmp_path):
    _, src_url = source
    with pytest.raises(CopyError, match="missing tables"):
        copy_database(src_url, f"sqlite:///{tmp_path / 'blank.db'}", check_version=False)


def test_refuses_when_schema_versions_differ(source, tmp_path):
    app, src_url = source
    _, dst_url = empty_sqlite_target(tmp_path)
    with app.app_context(), pytest.raises(CopyError, match="schema version"):
        copy_database(src_url, dst_url)  # no alembic_version table: neither database was made by `flask db upgrade`


def test_failure_leaves_the_target_empty(source, tmp_path, monkeypatch):
    _, src_url = source
    _, dst_url = empty_sqlite_target(tmp_path)

    def boom(conn):
        raise RuntimeError("disk full, say")

    monkeypatch.setattr(dbcopy, "_reset_sequences", boom)
    with pytest.raises(RuntimeError):
        copy_database(src_url, dst_url, check_version=False)
    assert sum(counts(dst_url).values()) == 0


@needs_pg
def test_sqlite_to_postgres_exact_and_ids_keep_counting(source, tmp_path):
    _, src_url = source
    pg_app = make_app(PG, tmp_path, "pg")  # creates the schema on PostgreSQL
    report = copy_database(src_url, PG, check_version=False)
    assert report.ok
    assert counts(src_url) == counts(PG)
    with pg_app.app_context():
        highest = db.session.query(db.func.max(User.id)).scalar()
        newcomer = User(email="new@example.com", full_name="New Person", phone="+251911000001", city="Hawassa")
        newcomer.set_password("secret123")
        db.session.add(newcomer)
        db.session.commit()
        assert newcomer.id == highest + 1  # the sequence was moved past the copied ids
        paid = Payment.query.filter_by(status="paid").first()
        assert str(paid.amount) == "2500.50"

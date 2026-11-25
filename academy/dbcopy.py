"""Copy a whole academy database from one engine to another (SQLite to PostgreSQL), and prove the copy is exact.

Used by ``flask migrate-to-postgres`` and deploy/migrate-to-postgres.sh. Design rules:

* The target must already have the schema (created by ``flask db upgrade``, the same path every server uses) and must be empty.
* The copy is ONE transaction on the target: either every row arrives or nothing does.
* Tables are copied in foreign-key order using the application's own table definitions, so SQLite's loose types
  (text dates, 0/1 booleans, floats) are converted to proper PostgreSQL types by SQLAlchemy.
* After the copy, every table's row count and a fingerprint of its content are compared between source and target.
* Sequences are moved past the highest id so the next new row does not collide with an existing one.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Integer, create_engine, func, inspect, select, text

from .models import db

SKIP_TABLES = {"alembic_version"}  # set by `flask db upgrade` on the target
BATCH = 500


class CopyError(Exception):
    """The copy cannot start or did not verify. The message says what to do."""


@dataclass
class TableReport:
    name: str
    source_rows: int = 0
    target_rows: int = 0
    source_digest: str = ""
    target_digest: str = ""

    @property
    def ok(self):
        return self.source_rows == self.target_rows and self.source_digest == self.target_digest


@dataclass
class CopyReport:
    tables: list[TableReport] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self):
        return all(t.ok for t in self.tables)

    @property
    def total_rows(self):
        return sum(t.source_rows for t in self.tables)


def _tables():
    return [t for t in db.metadata.sorted_tables if t.name not in SKIP_TABLES]


def _norm(value):
    """One canonical text form per value, so SQLite and PostgreSQL results compare equal."""
    if value is None:
        return "~"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="microseconds") if value.tzinfo is None else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, float, int)):
        d = Decimal(str(value))
        return format(d.normalize(), "f") if d != 0 else "0"
    return str(value)


def _fingerprint(conn, table):
    """(row count, sha256 of every row's normalised values in primary-key order)."""
    pk = list(table.primary_key.columns) or list(table.columns)
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute(select(table).order_by(*pk)):
        digest.update("\x1f".join(_norm(v) for v in row).encode("utf-8"))
        digest.update(b"\x1e")
        count += 1
    return count, digest.hexdigest()


def _head_revision():
    from alembic.script import ScriptDirectory
    from flask import current_app

    directory = current_app.extensions["migrate"].directory
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", directory)
    return ScriptDirectory.from_config(cfg).get_current_head()


def _version(conn):
    try:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - table missing
        return None


def check_ready(source_conn, target_conn, head=None):
    """Raise CopyError unless the source is current, the target has the schema, and the target is empty."""
    if head:
        for label, conn in (("source", source_conn), ("target", target_conn)):
            version = _version(conn)
            if version != head:
                raise CopyError(
                    f"The {label} database is at schema version {version or 'none'}, the code expects {head}. "
                    f"Run `flask db upgrade` against the {label} first."
                )
    existing = set(inspect(target_conn).get_table_names())
    missing = [t.name for t in _tables() if t.name not in existing]
    if missing:
        raise CopyError(f"The target is missing tables {missing}. Run `flask db upgrade` against it first.")
    filled = {t.name: n for t in _tables() if (n := target_conn.execute(select(func.count()).select_from(t)).scalar())}
    if filled:
        raise CopyError(f"The target is not empty ({filled}). Refusing to copy over existing data.")


def _reset_sequences(conn):
    """Move each serial sequence past the highest id (PostgreSQL only)."""
    if conn.dialect.name != "postgresql":
        return
    for t in _tables():
        pk = list(t.primary_key.columns)
        if len(pk) == 1 and isinstance(pk[0].type, Integer) and not isinstance(pk[0].type, Boolean):
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:tbl, :col), COALESCE((SELECT MAX({col}) FROM {tbl}), 1), "
                    "(SELECT MAX({col}) FROM {tbl}) IS NOT NULL)".format(col=f'"{pk[0].name}"', tbl=f'"{t.name}"')
                ),
                {"tbl": f'"{t.name}"', "col": pk[0].name},
            )


def copy_database(source_url, target_url, *, dry_run=False, check_version=True, progress=None):
    """Copy every table. Returns a CopyReport; raises CopyError if the databases are not ready or the copy does not verify."""
    head = _head_revision() if check_version else None
    source = create_engine(source_url)
    target = create_engine(target_url)
    report = CopyReport(dry_run=dry_run)
    try:
        with source.connect() as src, target.connect() as dst:
            tx = dst.begin()  # one transaction for the checks and the whole copy
            try:
                check_ready(src, dst, head)
                for table in _tables():
                    report.tables.append(
                        TableReport(table.name, source_rows=src.execute(select(func.count()).select_from(table)).scalar())
                    )
                if dry_run:
                    tx.rollback()
                    return report
                for table, row_report in zip(_tables(), report.tables):
                    rows = []
                    for row in src.execute(select(table)):
                        rows.append(dict(row._mapping))
                        if len(rows) >= BATCH:
                            dst.execute(table.insert(), rows)
                            rows = []
                    if rows:
                        dst.execute(table.insert(), rows)
                    if progress:
                        progress(table.name, row_report.source_rows)
                _reset_sequences(dst)
                for table, row_report in zip(_tables(), report.tables):
                    row_report.source_rows, row_report.source_digest = _fingerprint(src, table)
                    row_report.target_rows, row_report.target_digest = _fingerprint(dst, table)
                if not report.ok:
                    bad = [t.name for t in report.tables if not t.ok]
                    raise CopyError(f"Verification failed for {bad}. Nothing was copied (the transaction was rolled back).")
                tx.commit()
            except Exception:
                if tx.is_active:
                    tx.rollback()
                raise
    finally:
        source.dispose()
        target.dispose()
    return report

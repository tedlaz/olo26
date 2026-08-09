import sqlite3
from pathlib import Path

from sqlalchemy import inspect, text

from .extensions import db


def _backup_sqlite_database(label):
    if db.engine.url.get_backend_name() != "sqlite":
        return None
    database = db.engine.url.database
    if not database or database == ":memory:":
        return None
    source_path = Path(database)
    if not source_path.is_file():
        return None
    backup_path = source_path.with_name(f"{source_path.stem}.{label}.db")
    if backup_path.exists():
        return backup_path
    with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    return backup_path


def normalize_legacy_period_ids():
    """Align imported period PKs with legacy IDs and move newer periods after them."""
    period_columns = {column["name"] for column in inspect(db.engine).get_columns("periods")}
    if "legacy_id" not in period_columns:
        return None
    rows = (
        db.session.execute(text("SELECT id, legacy_id FROM periods ORDER BY id")).mappings().all()
    )
    legacy_rows = [row for row in rows if row["legacy_id"] is not None]
    if not legacy_rows:
        return None

    legacy_ids = [row["legacy_id"] for row in legacy_rows]
    if len(legacy_ids) != len(set(legacy_ids)) or any(item <= 0 for item in legacy_ids):
        raise RuntimeError("Τα legacy IDs των περιόδων δεν είναι μοναδικά και θετικά.")

    desired_ids = {row["id"]: row["legacy_id"] for row in legacy_rows}
    next_id = max(legacy_ids) + 1
    for row in (row for row in rows if row["legacy_id"] is None):
        desired_ids[row["id"]] = next_id
        next_id += 1
    if all(old_id == new_id for old_id, new_id in desired_ids.items()):
        return None

    backup_path = _backup_sqlite_database("before-period-id-migration")
    temporary_base = max(max(desired_ids), max(desired_ids.values())) + 1_000_000
    temporary_ids = {
        old_id: temporary_base + index for index, old_id in enumerate(desired_ids, start=1)
    }

    db.session.execute(text("PRAGMA defer_foreign_keys=ON"))
    for old_id, temporary_id in temporary_ids.items():
        parameters = {"old_id": old_id, "new_id": temporary_id}
        db.session.execute(text("UPDATE periods SET id=:new_id WHERE id=:old_id"), parameters)
        db.session.execute(
            text("UPDATE expenses SET period_id=:new_id WHERE period_id=:old_id"), parameters
        )
        db.session.execute(
            text("UPDATE allocations SET period_id=:new_id WHERE period_id=:old_id"),
            parameters,
        )
    for old_id, temporary_id in temporary_ids.items():
        parameters = {"old_id": temporary_id, "new_id": desired_ids[old_id]}
        db.session.execute(text("UPDATE periods SET id=:new_id WHERE id=:old_id"), parameters)
        db.session.execute(
            text("UPDATE expenses SET period_id=:new_id WHERE period_id=:old_id"), parameters
        )
        db.session.execute(
            text("UPDATE allocations SET period_id=:new_id WHERE period_id=:old_id"),
            parameters,
        )

    violations = db.session.execute(text("PRAGMA foreign_key_check")).all()
    if violations:
        db.session.rollback()
        raise RuntimeError(f"Η αλλαγή αρίθμησης παραβίασε foreign keys: {violations}")
    db.session.commit()
    return backup_path


def drop_period_legacy_id():
    """Remove the compatibility column after IDs have been normalized."""
    period_columns = {column["name"] for column in inspect(db.engine).get_columns("periods")}
    if "legacy_id" not in period_columns:
        return None
    backup_path = _backup_sqlite_database("before-period-legacy-id-drop")
    db.session.execute(text("ALTER TABLE periods DROP COLUMN legacy_id"))
    db.session.commit()
    return backup_path

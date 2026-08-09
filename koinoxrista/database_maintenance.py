import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .extensions import db

_maintenance_lock = threading.Lock()

REQUIRED_TABLES = {
    "users",
    "buildings",
    "building_memberships",
    "manager_terms",
    "apartments",
    "expense_categories",
    "millesimals",
    "periods",
    "expenses",
    "allocations",
    "audit_log",
}

REQUIRED_COLUMNS = {
    "users": {"id", "email", "password_hash", "is_system_admin", "auth_version"},
    "buildings": {"id", "name", "address", "postal_code"},
    "periods": {"id", "building_id", "issue_date", "status"},
}


class DatabaseMaintenanceError(RuntimeError):
    pass


def application_database_path():
    if db.engine.url.get_backend_name() != "sqlite":
        raise DatabaseMaintenanceError("Η λειτουργία υποστηρίζεται μόνο για SQLite.")
    database = db.engine.url.database
    if not database or database == ":memory:":
        raise DatabaseMaintenanceError("Δεν υπάρχει αρχείο SQLite για backup.")
    return Path(database).resolve()


def backup_directory():
    path = application_database_path().parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _readonly_connection(path):
    return sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)


def validate_database(path):
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise DatabaseMaintenanceError("Το αρχείο backup είναι κενό ή δεν υπάρχει.")
        with closing(_readonly_connection(path)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise DatabaseMaintenanceError(f"Αποτυχία integrity check: {integrity}")
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            missing_tables = REQUIRED_TABLES - tables
            if missing_tables:
                raise DatabaseMaintenanceError(
                    f"Το αρχείο δεν είναι backup της εφαρμογής. Λείπουν: "
                    f"{', '.join(sorted(missing_tables))}"
                )
            for table, required in REQUIRED_COLUMNS.items():
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                missing_columns = required - columns
                if missing_columns:
                    raise DatabaseMaintenanceError(
                        f"Ο πίνακας {table} δεν έχει τις στήλες: "
                        f"{', '.join(sorted(missing_columns))}"
                    )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise DatabaseMaintenanceError(
                    f"Το backup έχει {len(violations)} παραβιάσεις foreign keys."
                )
            admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE is_system_admin = 1"
            ).fetchone()[0]
            if admins < 1:
                raise DatabaseMaintenanceError("Το backup δεν περιέχει system admin.")
    except DatabaseMaintenanceError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise DatabaseMaintenanceError(f"Μη έγκυρο αρχείο SQLite: {exc}") from exc
    return path


def create_backup(prefix="manual"):
    with _maintenance_lock:
        source_path = application_database_path()
        if not source_path.is_file():
            raise DatabaseMaintenanceError("Δεν βρέθηκε η ενεργή βάση δεδομένων.")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        destination = backup_directory() / f"koinoxrista-{prefix}-{timestamp}.db"
        try:
            with (
                closing(_readonly_connection(source_path)) as source,
                closing(sqlite3.connect(destination)) as target,
            ):
                source.backup(target)
            validate_database(destination)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            if isinstance(exc, DatabaseMaintenanceError):
                raise
            raise DatabaseMaintenanceError(f"Αποτυχία δημιουργίας backup: {exc}") from exc
        return destination


def save_restore_upload(file_storage, max_bytes):
    destination = application_database_path().parent / f".restore-{uuid4().hex}.db"
    total = 0
    try:
        with destination.open("wb") as output:
            while chunk := file_storage.stream.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise DatabaseMaintenanceError(
                        f"Το backup υπερβαίνει το όριο των {max_bytes // (1024 * 1024)} MB."
                    )
                output.write(chunk)
        validate_database(destination)
        return destination
    except Exception as exc:
        destination.unlink(missing_ok=True)
        if isinstance(exc, DatabaseMaintenanceError):
            raise
        raise DatabaseMaintenanceError(f"Αποτυχία αποθήκευσης backup: {exc}") from exc


def restore_database(upload_path):
    upload_path = validate_database(upload_path)
    with _maintenance_lock:
        active_path = application_database_path()
        pre_restore = create_backup_without_lock("pre-restore")
        db.session.remove()
        db.engine.dispose()
        try:
            with (
                closing(_readonly_connection(upload_path)) as source,
                closing(sqlite3.connect(active_path)) as target,
            ):
                source.backup(target)
            from . import initialize_database

            initialize_database()
            validate_database(active_path)
        except Exception as exc:
            db.session.remove()
            db.engine.dispose()
            try:
                with (
                    closing(_readonly_connection(pre_restore)) as source,
                    closing(sqlite3.connect(active_path)) as target,
                ):
                    source.backup(target)
                from . import initialize_database

                initialize_database()
            except Exception as rollback_exc:
                raise DatabaseMaintenanceError(
                    f"Το restore απέτυχε και απέτυχε και η επαναφορά ασφαλείας: {rollback_exc}"
                ) from exc
            raise DatabaseMaintenanceError(
                f"Το restore ακυρώθηκε και επανήλθε η προηγούμενη βάση: {exc}"
            ) from exc
        finally:
            Path(upload_path).unlink(missing_ok=True)
        return pre_restore


def create_backup_without_lock(prefix):
    source_path = application_database_path()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_directory() / f"koinoxrista-{prefix}-{timestamp}.db"
    try:
        with (
            closing(_readonly_connection(source_path)) as source,
            closing(sqlite3.connect(destination)) as target,
        ):
            source.backup(target)
        validate_database(destination)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        if isinstance(exc, DatabaseMaintenanceError):
            raise
        raise DatabaseMaintenanceError(f"Αποτυχία pre-restore backup: {exc}") from exc
    return destination


def list_backups():
    backups = []
    for path in sorted(backup_directory().glob("koinoxrista-*.db"), reverse=True):
        stat = path.stat()
        backups.append(
            {
                "name": path.name,
                "size_mb": stat.st_size / (1024 * 1024),
                "created_at": datetime.fromtimestamp(stat.st_mtime),
            }
        )
    return backups


def local_backup_path(filename):
    if Path(filename).name != filename or not filename.startswith("koinoxrista-"):
        raise DatabaseMaintenanceError("Μη έγκυρο όνομα backup.")
    path = backup_directory() / filename
    if path.suffix.lower() != ".db" or not path.is_file():
        raise DatabaseMaintenanceError("Το τοπικό backup δεν βρέθηκε.")
    return path


def delete_local_backup(filename):
    with _maintenance_lock:
        path = local_backup_path(filename)
        try:
            path.unlink()
        except OSError as exc:
            raise DatabaseMaintenanceError(f"Δεν ήταν δυνατή η διαγραφή: {exc}") from exc
        return path.name

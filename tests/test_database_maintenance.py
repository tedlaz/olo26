from io import BytesIO

from sqlalchemy import text

from koinoxrista.extensions import db
from koinoxrista.models import Building, User


def test_admin_can_backup_and_restore_database(app, client):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        building = Building(
            name="Backup Building",
            address="Backup Street 1",
            postal_code="12345",
            created_by_id=admin.id,
        )
        db.session.add(building)
        db.session.commit()
        building_id = building.id

    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    page = client.get("/admin/database")
    assert page.status_code == 200
    assert "Backup / Restore" in page.text
    assert 'hx-boost="false"' in page.text
    assert "data-backup-download" in page.text

    prepared = client.post("/admin/database/backup/prepare")
    assert prepared.status_code == 200
    prepared_data = prepared.get_json()
    assert prepared_data["download_url"].startswith("/admin/database/backups/")
    fragment = client.get("/admin/database/backups/fragment")
    assert fragment.status_code == 200
    assert prepared_data["filename"] in fragment.text
    prepared_download = client.get(prepared_data["download_url"])
    assert prepared_download.data.startswith(b"SQLite format 3")
    prepared_download.close()

    backup = client.post("/admin/database/backup")
    assert backup.status_code == 200
    assert backup.mimetype == "application/vnd.sqlite3"
    assert backup.data.startswith(b"SQLite format 3")

    htmx_backup = client.post("/admin/database/backup", headers={"HX-Request": "true"})
    assert htmx_backup.status_code == 200
    assert htmx_backup.headers["HX-Redirect"].startswith("/admin/database/backups/")
    htmx_download = client.get(htmx_backup.headers["HX-Redirect"])
    assert htmx_download.status_code == 200
    assert htmx_download.data.startswith(b"SQLite format 3")
    backup_url = htmx_backup.headers["HX-Redirect"]
    backup_filename = backup_url.rsplit("/", 1)[-1]
    htmx_download.close()
    deleted = client.post(
        f"/admin/database/backups/{backup_filename}/delete",
        headers={"HX-Request": "true"},
    )
    assert deleted.status_code == 200
    assert b"<html" not in deleted.data
    assert f"<strong>{backup_filename}</strong>" not in deleted.text
    assert "διαγράφηκε" in deleted.text
    assert client.get(backup_url).status_code == 404

    with app.app_context():
        db.session.delete(db.session.get(Building, building_id))
        db.session.commit()
        assert db.session.get(Building, building_id) is None

    restored = client.post(
        "/admin/database/restore",
        data={
            "database_file": (BytesIO(backup.data), "backup.db"),
            "password": "changeme",
            "confirmation": "RESTORE",
        },
        content_type="multipart/form-data",
    )
    assert restored.status_code == 302
    assert restored.headers["Location"].endswith("/auth/login")

    with app.app_context():
        restored_building = db.session.get(Building, building_id)
        assert restored_building is not None
        assert restored_building.name == "Backup Building"
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_restore_rejects_invalid_file_without_changing_database(app, client):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        original_count = db.session.scalar(db.select(db.func.count(User.id)))
        db.session.commit()

    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    response = client.post(
        "/admin/database/restore",
        data={
            "database_file": (BytesIO(b"not a sqlite database"), "invalid.db"),
            "password": "changeme",
            "confirmation": "RESTORE",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/database")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(User.id))) == original_count


def test_database_maintenance_is_system_admin_only(app, client):
    with app.app_context():
        user = User(email="viewer@example.com", display_name="Viewer")
        user.set_password("a-secure-password")
        db.session.add(user)
        db.session.commit()

    client.post(
        "/auth/login",
        data={"email": "viewer@example.com", "password": "a-secure-password"},
    )
    assert client.get("/admin/database").status_code == 403
    assert client.post("/admin/database/backup").status_code == 403

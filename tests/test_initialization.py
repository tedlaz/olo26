import pytest

from koinoxrista import configured_secret_key, create_app
from koinoxrista.extensions import db
from koinoxrista.models import User


def test_missing_database_is_initialized_automatically(tmp_path):
    database = tmp_path / "missing.db"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
            "SECRET_KEY": "test-secret",
        }
    )

    assert database.is_file()
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        assert admin is not None
        assert admin.must_change_password
        assert admin.check_password("changeme")


def test_health_endpoint_and_security_headers(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_secret_key_can_be_loaded_from_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "flask_secret"
    secret_file.write_text("a" * 64, encoding="utf-8")
    monkeypatch.setenv("SECRET_KEY_FILE", str(secret_file))
    monkeypatch.delenv("SECRET_KEY", raising=False)

    assert configured_secret_key() == "a" * 64


def test_production_refuses_to_start_without_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECRET_KEY_FILE", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Production requires"):
        configured_secret_key()

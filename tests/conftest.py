import pytest

from koinoxrista import create_app
from koinoxrista.extensions import db
from koinoxrista.models import User


@pytest.fixture
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret",
            "AUTO_INIT_DATABASE": False,
        }
    )
    with application.app_context():
        db.create_all()
        admin = User(
            email="admin@admin.app",
            display_name="Administrator",
            is_system_admin=True,
            can_create_building=True,
            must_change_password=True,
        )
        admin.set_password("changeme")
        db.session.add(admin)
        db.session.commit()
    yield application


@pytest.fixture
def client(app):
    return app.test_client()

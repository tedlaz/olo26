from koinoxrista import create_app
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

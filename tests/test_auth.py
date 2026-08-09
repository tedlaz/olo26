from koinoxrista.extensions import db
from koinoxrista.models import User


def test_admin_must_change_initial_password(client):
    response = client.post(
        "/auth/login",
        data={"email": "ADMIN@ADMIN.APP", "password": "changeme"},
        follow_redirects=False,
    )
    assert response.headers["Location"].endswith("/auth/change-password")

    blocked = client.get("/", follow_redirects=False)
    assert blocked.headers["Location"].endswith("/auth/change-password")

    changed = client.post(
        "/auth/change-password",
        data={
            "current_password": "changeme",
            "password": "a-new-secure-password",
            "confirm_password": "a-new-secure-password",
        },
        follow_redirects=False,
    )
    assert changed.headers["Location"].endswith("/")


def test_password_change_invalidates_old_session(app, client):
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    with client.session_transaction() as session:
        old_user_id = session["_user_id"]
    client.post(
        "/auth/change-password",
        data={
            "current_password": "changeme",
            "password": "a-new-secure-password",
            "confirm_password": "a-new-secure-password",
        },
    )
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        assert old_user_id != user.get_id()

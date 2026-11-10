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


def test_two_character_password_is_accepted(client):
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    data = {"current_password": "changeme", "password": "ab", "confirm_password": "ab"}
    response = client.post("/auth/change-password", data=data, follow_redirects=False)
    assert response.headers["Location"].endswith("/")
    data = {"current_password": "ab", "password": "a", "confirm_password": "a"}
    assert client.post("/auth/change-password", data=data).status_code == 200


def test_each_user_picks_own_theme(app, client):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        other = User(email="other@example.com", display_name="Other")
        other.set_password("pw")
        db.session.add(other)
        db.session.commit()

    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    assert 'data-theme="light"' in client.get("/").text
    client.post("/auth/theme", data={"theme": "midnight"})
    client.post("/auth/theme", data={"theme": "not-a-theme"})
    assert 'data-theme="midnight"' in client.get("/").text

    client.post("/auth/logout")
    client.post("/auth/login", data={"email": "other@example.com", "password": "pw"})
    assert 'data-theme="light"' in client.get("/").text

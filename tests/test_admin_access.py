from koinoxrista.extensions import db
from koinoxrista.models import Building, BuildingMembership, ManagerTerm, User


def make_user_and_buildings(app):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        user = User(email="user@example.com", display_name="Test User")
        user.set_password("pw")
        first = Building(name="Α", address="Οδός 1", postal_code="1", created_by_id=admin.id)
        second = Building(name="Β", address="Οδός 2", postal_code="2", created_by_id=admin.id)
        db.session.add_all([user, first, second])
        db.session.commit()
        return user.id, first.id, second.id


def memberships(app, user_id):
    with app.app_context():
        return [
            (m.building_id, m.role)
            for m in db.session.scalars(
                db.select(BuildingMembership).where(BuildingMembership.user_id == user_id)
            )
        ]


def test_admin_assigns_single_building_and_user_type(app, client):
    user_id, first_id, second_id = make_user_and_buildings(app)
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    form = {"display_name": "Test User", "email": "user@example.com", "is_active": "on"}

    client.post(f"/admin/users/{user_id}/permissions", data={**form, "building_id": first_id})
    page = client.get("/admin/users")
    assert "dlg-user-" in page.text and "Απλός χρήστης" in page.text
    assert memberships(app, user_id) == [(first_id, "viewer")]

    client.post(f"/admin/users/{user_id}/permissions", data={**form, "building_id": second_id})
    assert memberships(app, user_id) == [(second_id, "viewer")]

    client.post(f"/admin/users/{user_id}/permissions", data={**form, "user_type": "admin"})
    with app.app_context():
        assert db.session.get(User, user_id).is_system_admin
    assert memberships(app, user_id) == []


def test_admin_cannot_demote_self(app, client):
    make_user_and_buildings(app)
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    with app.app_context():
        admin_id = db.session.scalar(db.select(User.id).where(User.email == "admin@admin.app"))
    client.post(
        f"/admin/users/{admin_id}/permissions",
        data={"display_name": "A", "email": "admin@admin.app", "user_type": "user"},
    )
    with app.app_context():
        assert db.session.get(User, admin_id).is_system_admin


def test_simple_user_is_read_only_and_limited_to_their_building(app, client):
    user_id, first_id, second_id = make_user_and_buildings(app)
    with app.app_context():
        # even a legacy "building_admin" membership only grants viewing now
        db.session.add(
            BuildingMembership(user_id=user_id, building_id=first_id, role="building_admin")
        )
        db.session.commit()
    client.post("/auth/login", data={"email": "user@example.com", "password": "pw"})

    page = client.get(f"/buildings/{first_id}")
    assert page.status_code == 200
    assert "data-open" not in page.text and "data-modal " not in page.text
    assert client.get(f"/buildings/{second_id}").status_code == 403
    assert client.get(f"/buildings/{first_id}/periods/new").status_code == 403
    assert client.get(f"/buildings/{first_id}/millesimals").status_code == 403
    assert client.post(f"/buildings/{first_id}/details", data={}).status_code == 403
    assert client.get("/buildings/new").status_code == 403
    assert client.get("/admin/users").status_code == 403


def test_building_page_has_tabs_with_periods_as_default(app, client):
    _, building_id, _ = make_user_and_buildings(app)
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    expected = {
        None: "Νέα περίοδος",
        "periods": "Νέα περίοδος",
        "apartments": "Νέο διαμέρισμα",
        "categories": "Νέα κατηγορία",
        "managers": "Δεν υπάρχουν διαχειριστές",
        "bogus": "Νέα περίοδος",
    }
    for tab, text in expected.items():
        page = client.get(f"/buildings/{building_id}", query_string={"tab": tab} if tab else {})
        assert page.status_code == 200
        assert text in page.text
        assert 'class="tabs"' in page.text


def test_any_active_admin_can_be_assigned_as_building_manager(app, client):
    user_id, building_id, _ = make_user_and_buildings(app)
    with app.app_context():
        other_admin = User(email="b@example.com", display_name="Β Admin", is_system_admin=True)
        other_admin.set_password("pw")
        db.session.add(other_admin)
        db.session.commit()
        other_admin_id = other_admin.id
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})

    page = client.get(f"/buildings/{building_id}?tab=managers").text
    assert "Β Admin (b@example.com) · Administrator" in page
    assert "user@example.com" not in page  # simple user without access to this building

    url = f"/buildings/{building_id}/managers"
    assert client.post(url, data={"user_id": user_id, "date_from": "2026-01-01"}).status_code == 400
    client.post(url, data={"user_id": other_admin_id, "date_from": "2026-01-01"})
    with app.app_context():
        term = db.session.scalar(
            db.select(ManagerTerm).where(ManagerTerm.building_id == building_id)
        )
        assert term.user_id == other_admin_id

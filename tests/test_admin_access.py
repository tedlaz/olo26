from koinoxrista.extensions import db
from koinoxrista.models import Building, BuildingMembership, User


def test_admin_can_see_change_and_remove_building_access(app, client):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        user = User(email="user@example.com", display_name="Test User")
        user.set_password("a-secure-password")
        building = Building(
            name="Κτίριο Δοκιμής",
            address="Οδός 1",
            postal_code="12345",
            created_by_id=admin.id,
        )
        db.session.add_all([user, building])
        db.session.commit()
        user_id = user.id
        building_id = building.id

    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    setup_after_building = client.get("/setup")
    assert setup_after_building.status_code == 302
    assert setup_after_building.headers["Location"].endswith("/")
    created = client.post(
        f"/admin/users/{user_id}/building-access",
        data={"building_id": building_id, "role": "viewer"},
    )
    assert created.status_code == 302

    page = client.get("/admin/users")
    assert "Κτίριο Δοκιμής" in page.text
    assert "Μόνο προβολή" in page.text

    client.post(
        f"/admin/users/{user_id}/building-access",
        data={"building_id": building_id, "role": "editor"},
    )
    with app.app_context():
        membership = db.session.scalar(
            db.select(BuildingMembership).where(
                BuildingMembership.user_id == user_id,
                BuildingMembership.building_id == building_id,
            )
        )
        assert membership.role == "editor"

    deleted = client.post(f"/admin/users/{user_id}/building-access/{building_id}/delete")
    assert deleted.status_code == 302
    with app.app_context():
        assert (
            db.session.scalar(
                db.select(BuildingMembership).where(
                    BuildingMembership.user_id == user_id,
                    BuildingMembership.building_id == building_id,
                )
            )
            is None
        )

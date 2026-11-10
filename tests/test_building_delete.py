from datetime import date
from decimal import Decimal

from koinoxrista.database_maintenance import list_backups
from koinoxrista.extensions import db
from koinoxrista.models import (
    Allocation,
    Apartment,
    AuditLog,
    Building,
    BuildingMembership,
    Expense,
    ExpenseCategory,
    ManagerTerm,
    Millesimal,
    Period,
    User,
)
from koinoxrista.services import build_allocations


def make_full_building(app):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        viewer = User(email="v@example.com", display_name="V")
        viewer.set_password("pw")
        building = Building(
            name="Προς διαγραφή", address="Α", postal_code="1", created_by_id=admin.id
        )
        keep = Building(name="Μένει", address="Β", postal_code="2", created_by_id=admin.id)
        category = ExpenseCategory(building=building, name="Καθαρισμός")
        apartment = Apartment(building=building, name="Δ1", number=1)
        db.session.add_all([viewer, building, keep, category, apartment])
        db.session.flush()
        db.session.add(Millesimal(apartment=apartment, category=category, value=1000))
        term = ManagerTerm(building_id=building.id, display_name="Δ", date_from=date(2026, 1, 1))
        period = Period(building=building, issue_date=date(2026, 8, 1), manager_term=term)
        db.session.add_all(
            [
                term,
                period,
                Expense(
                    period=period,
                    category=category,
                    invoice_date=date(2026, 8, 2),
                    invoice_number="1",
                    description="Δ",
                    amount=Decimal("10"),
                ),
                BuildingMembership(user_id=viewer.id, building_id=building.id, role="viewer"),
                AuditLog(action="x", building_id=building.id),
            ]
        )
        db.session.flush()
        build_allocations(period)
        db.session.commit()
        return building.id, keep.id


def count(model):
    return db.session.scalar(db.select(db.func.count()).select_from(model))


def test_building_delete_requires_name_and_password_then_removes_everything(app, client):
    building_id, keep_id = make_full_building(app)
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    url = f"/buildings/{building_id}/delete"

    client.post(url, data={"confirmation": "λάθος", "password": "changeme"})
    client.post(url, data={"confirmation": "Προς διαγραφή", "password": "λάθος"})
    with app.app_context():
        assert db.session.get(Building, building_id) is not None
        backups_before = len(list_backups())

    response = client.post(url, data={"confirmation": "Προς διαγραφή", "password": "changeme"})
    assert response.headers["Location"].endswith("/")
    with app.app_context():
        assert db.session.get(Building, building_id) is None
        assert db.session.get(Building, keep_id) is not None
        for model in (Allocation, Expense, Period, Millesimal, Apartment, ExpenseCategory):
            assert count(model) == 0, model
        assert count(ManagerTerm) == 0 and count(BuildingMembership) == 0
        assert (
            db.session.scalar(db.select(AuditLog).where(AuditLog.action == "x")).building_id is None
        )
        assert len(list_backups()) == backups_before + 1


def test_simple_user_cannot_delete_building(app, client):
    building_id, _ = make_full_building(app)
    client.post("/auth/login", data={"email": "v@example.com", "password": "pw"})
    response = client.post(
        f"/buildings/{building_id}/delete", data={"confirmation": "Προς διαγραφή", "password": "pw"}
    )
    assert response.status_code == 403


def test_last_active_admin_cannot_be_demoted_or_deactivated(app, client):
    make_full_building(app)
    with app.app_context():
        second = User(email="b@example.com", display_name="B", is_system_admin=True)
        second.set_password("pw")
        db.session.add(second)
        db.session.commit()
        second_id = second.id
    client.post("/auth/login", data={"email": "b@example.com", "password": "pw"})
    with app.app_context():
        first_id = db.session.scalar(db.select(User.id).where(User.email == "admin@admin.app"))
    form = {"display_name": "A", "email": "admin@admin.app", "user_type": "admin"}

    # two admins: deactivating the other one is allowed
    client.post(f"/admin/users/{first_id}/permissions", data=form)
    with app.app_context():
        assert not db.session.get(User, first_id).is_active_account

    # now b is the only active admin: it cannot lose the role through anyone
    client.post(f"/admin/users/{first_id}/permissions", data={**form, "is_active": "on"})
    client.post(
        f"/admin/users/{second_id}/permissions",
        data={"display_name": "B", "email": "b@example.com", "user_type": "user"},
    )
    with app.app_context():
        assert db.session.get(User, second_id).is_system_admin

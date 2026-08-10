from datetime import date

from koinoxrista.extensions import db
from koinoxrista.models import Building, ManagerTerm, Period, User


def create_period(app, *, status="draft"):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        building = Building(
            name="Κτίριο δοκιμής",
            address="Δοκιμής 1",
            postal_code="11111",
            created_by_id=admin.id,
        )
        db.session.add(building)
        db.session.flush()
        manager = ManagerTerm(
            building_id=building.id,
            user=admin,
            display_name="Νέος διαχειριστής",
            date_from=date(2026, 1, 1),
        )
        period = Period(
            building=building,
            issue_date=date(2026, 8, 1),
            comments="Αρχικά σχόλια",
            manager_term=manager,
            manager_name_snapshot=manager.display_name,
            status=status,
        )
        db.session.add(period)
        db.session.commit()
        return period.id


def login(client):
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})


def test_period_date_and_comments_can_be_edited(app, client):
    period_id = create_period(app)
    login(client)

    page = client.get(f"/periods/{period_id}")
    response = client.post(
        f"/periods/{period_id}/edit",
        data={"issue_date": "2026-08-15", "comments": "Ενημερωμένα σχόλια"},
        follow_redirects=False,
    )

    assert f'/periods/{period_id}/edit' in page.text
    assert "Διόρθωση στοιχείων" in page.text
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/periods/{period_id}")
    with app.app_context():
        period = db.session.get(Period, period_id)
        assert period.issue_date == date(2026, 8, 15)
        assert period.comments == "Ενημερωμένα σχόλια"
        assert period.manager_name_snapshot == "Νέος διαχειριστής"


def test_finalized_period_cannot_be_edited(app, client):
    period_id = create_period(app, status="finalized")
    login(client)

    page = client.get(f"/periods/{period_id}")
    get_response = client.get(f"/periods/{period_id}/edit")
    post_response = client.post(
        f"/periods/{period_id}/edit",
        data={"issue_date": "2026-08-15", "comments": "Νέα σχόλια"},
    )

    assert "Διόρθωση στοιχείων" not in page.text
    assert get_response.status_code == 409
    assert post_response.status_code == 409

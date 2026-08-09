from datetime import date, timedelta

from koinoxrista.extensions import db
from koinoxrista.models import Building, Period, User


def test_building_periods_are_paginated(app, client):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        building = Building(
            name="Πολυκατοικία",
            address="Δοκιμής 1",
            postal_code="12345",
            created_by_id=admin.id,
        )
        db.session.add(building)
        db.session.flush()
        for offset in range(31):
            db.session.add(
                Period(
                    building=building,
                    issue_date=date(2024, 1, 1) + timedelta(days=offset),
                    comments=f"Περίοδος {offset + 1}",
                )
            )
        db.session.commit()
        building_id = building.id
        period_id = db.session.scalar(db.select(Period.id).order_by(Period.issue_date))

    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    first_page = client.get(f"/buildings/{building_id}")
    last_page = client.get(f"/buildings/{building_id}?page=3")

    assert first_page.status_code == 200
    assert first_page.data.count(b'href="/periods/') == 15
    assert "Πίσω στα κτίρια" in first_page.text
    assert last_page.status_code == 200
    assert last_page.data.count(b'href="/periods/') == 1

    period_page = client.get(f"/periods/{period_id}")
    assert period_page.status_code == 200
    assert "Πίσω στο Πολυκατοικία" in period_page.text
    assert f"/buildings/{building_id}?page=3" in period_page.text

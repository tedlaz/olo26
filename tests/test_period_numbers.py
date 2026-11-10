from datetime import date

from sqlalchemy import inspect, text

from koinoxrista.data_migrations import add_period_numbers
from koinoxrista.extensions import db
from koinoxrista.models import Building, Period, User


def make_buildings(admin_id):
    first = Building(name="Α", address="1", postal_code="1", created_by_id=admin_id)
    second = Building(name="Β", address="2", postal_code="2", created_by_id=admin_id)
    db.session.add_all([first, second])
    return first, second


def drop_number_column():
    # rebuild the table as it was before the migration (no number column)
    columns = (
        "id, building_id, issue_date, comments, status, manager_term_id,"
        " manager_name_snapshot, finalized_at, reopened_count"
    )
    db.session.execute(
        text(
            "CREATE TABLE periods_old (id INTEGER PRIMARY KEY,"
            " building_id INTEGER NOT NULL REFERENCES buildings (id),"
            " issue_date DATE NOT NULL, comments TEXT NOT NULL DEFAULT '',"
            " status VARCHAR(20) NOT NULL DEFAULT 'draft', manager_term_id INTEGER,"
            " manager_name_snapshot VARCHAR(100) NOT NULL DEFAULT '', finalized_at DATETIME,"
            " reopened_count INTEGER NOT NULL DEFAULT 0, UNIQUE (building_id, issue_date))"
        )
    )
    db.session.execute(text(f"INSERT INTO periods_old ({columns}) SELECT {columns} FROM periods"))
    db.session.execute(text("DROP TABLE periods"))
    db.session.execute(text("ALTER TABLE periods_old RENAME TO periods"))
    db.session.commit()


def numbers(building):
    return [
        (period.issue_date.month, period.number)
        for period in db.session.scalars(
            db.select(Period).where(Period.building_id == building.id).order_by(Period.id)
        )
    ]


def test_period_numbers_start_at_one_in_every_building(app):
    with app.app_context():
        admin_id = db.session.scalar(db.select(User.id))
        first, second = make_buildings(admin_id)
        # several new periods in one flush, for a building that is itself new
        db.session.add_all(
            [Period(building=first, issue_date=date(2026, month, 1)) for month in (1, 2)]
        )
        db.session.commit()
        db.session.add(Period(building=second, issue_date=date(2026, 1, 1)))
        db.session.commit()
        db.session.add(Period(building_id=first.id, issue_date=date(2026, 3, 1)))
        db.session.commit()

        assert numbers(first) == [(1, 1), (2, 2), (3, 3)]
        assert numbers(second) == [(1, 1)]


def test_migration_numbers_existing_periods_per_building_in_creation_order(app):
    with app.app_context():
        admin_id = db.session.scalar(db.select(User.id))
        first, second = make_buildings(admin_id)
        db.session.flush()
        # interleaved creation across buildings, as in a real database
        for building, month in ((first, 1), (second, 1), (first, 2), (second, 2), (first, 3)):
            db.session.add(Period(building_id=building.id, issue_date=date(2026, month, 1)))
            db.session.flush()
        db.session.commit()
        drop_number_column()
        assert "number" not in {c["name"] for c in inspect(db.engine).get_columns("periods")}
        db.session.expire_all()

        add_period_numbers()
        add_period_numbers()  # idempotent

        assert numbers(first) == [(1, 1), (2, 2), (3, 3)]
        assert numbers(second) == [(1, 1), (2, 2)]
        indexes = {index["name"] for index in inspect(db.engine).get_indexes("periods")}
        assert "uq_periods_building_number" in indexes
        db.session.add(Period(building_id=second.id, issue_date=date(2026, 3, 1)))
        db.session.commit()
        assert numbers(second)[-1] == (3, 3)


def test_period_page_and_pdf_show_building_number(app, client):
    from koinoxrista.pdf_reports import period_reference

    with app.app_context():
        admin = db.session.scalar(db.select(User))
        admin.must_change_password = False
        first, second = make_buildings(admin.id)
        db.session.add(Period(building=first, issue_date=date(2026, 1, 1)))
        db.session.commit()
        period = Period(building=second, issue_date=date(2026, 1, 31), comments="Ιανουάριος")
        db.session.add(period)
        db.session.commit()
        assert period.id == 2 and period.number == 1
        assert period_reference(period).endswith("(No: 1)")
        building_id, period_id = second.id, period.id

    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})
    assert "No <strong>1</strong>" in client.get(f"/periods/{period_id}").text
    assert "<td>1</td>" in client.get(f"/buildings/{building_id}").text


def test_migration_keeps_old_system_numbers_for_imported_building(app):
    from koinoxrista.models import utcnow

    with app.app_context():
        admin_id = db.session.scalar(db.select(User.id))
        legacy, other = make_buildings(admin_id)
        legacy.legacy_imported_at = utcnow()
        db.session.flush()
        # old system numbers 1, 2, 4 (3 was deleted there); id 3 and 5 belong to another building
        for period_id, building, month in (
            (1, legacy, 1),
            (2, legacy, 2),
            (3, other, 1),
            (4, legacy, 3),
            (5, other, 2),
        ):
            db.session.add(
                Period(
                    id=period_id,
                    number=period_id,
                    building_id=building.id,
                    issue_date=date(2026, month, 1),
                )
            )
        db.session.commit()
        drop_number_column()
        db.session.expire_all()

        add_period_numbers()

        assert numbers(legacy) == [(1, 1), (2, 2), (3, 4)]
        assert numbers(other) == [(1, 1), (2, 2)]
        db.session.add(Period(building_id=legacy.id, issue_date=date(2026, 4, 1)))
        db.session.commit()
        assert numbers(legacy)[-1] == (4, 5)

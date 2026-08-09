from datetime import date
from decimal import Decimal

from sqlalchemy import inspect, text

from koinoxrista.data_migrations import drop_period_legacy_id, normalize_legacy_period_ids
from koinoxrista.extensions import db
from koinoxrista.models import (
    Allocation,
    Apartment,
    Building,
    Expense,
    ExpenseCategory,
    Period,
    User,
)


def test_period_id_migration_preserves_children_and_moves_new_periods(app):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        building = Building(
            name="Migration",
            address="Test 1",
            postal_code="12345",
            created_by_id=admin.id,
        )
        category = ExpenseCategory(building=building, name="Καθαριότητα")
        apartment = Apartment(
            building=building,
            name="Διαμέρισμα",
            number=1,
            floor=1,
            square_meters=100,
        )
        first = Period(
            id=1,
            building=building,
            issue_date=date(2024, 1, 31),
        )
        moved = Period(
            id=2,
            building=building,
            issue_date=date(2024, 2, 29),
        )
        new = Period(id=3, building=building, issue_date=date(2024, 3, 31))
        expense = Expense(
            period=moved,
            category=category,
            invoice_date=date(2024, 2, 20),
            invoice_number="1",
            description="Test",
            amount=Decimal("10.00"),
        )
        allocation = Allocation(
            period=moved,
            apartment=apartment,
            category=category,
            apartment_name="1. Διαμέρισμα",
            category_name="Καθαριότητα",
            millesimals=1000,
            category_total=Decimal("10.00"),
            amount=Decimal("10.00"),
        )
        db.session.add_all([building, category, apartment, first, moved, new, expense, allocation])
        db.session.commit()
        db.session.execute(text("ALTER TABLE periods ADD COLUMN legacy_id INTEGER"))
        db.session.execute(text("UPDATE periods SET legacy_id=1 WHERE id=1"))
        db.session.execute(text("UPDATE periods SET legacy_id=3 WHERE id=2"))
        db.session.commit()

        backup = normalize_legacy_period_ids()
        assert backup and backup.is_file()
        db.session.expunge_all()

        periods = db.session.execute(text("SELECT id, legacy_id FROM periods ORDER BY id")).all()
        assert periods == [(1, 1), (3, 3), (4, None)]
        assert db.session.scalar(db.select(Expense.period_id)) == 3
        assert db.session.scalar(db.select(Allocation.period_id)) == 3
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []

        schema_backup = drop_period_legacy_id()
        assert schema_backup and schema_backup.is_file()
        assert "legacy_id" not in {
            column["name"] for column in inspect(db.engine).get_columns("periods")
        }
        assert db.session.scalars(db.select(Period.id).order_by(Period.id)).all() == [1, 3, 4]

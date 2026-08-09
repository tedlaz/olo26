import hashlib
from datetime import date
from pathlib import Path

from koinoxrista.extensions import db
from koinoxrista.legacy import import_legacy
from koinoxrista.models import (
    Allocation,
    Building,
    BuildingMembership,
    Expense,
    ManagerTerm,
    Period,
    User,
)
from koinoxrista.pdf_reports import (
    create_period_report,
    create_receipts_report,
    period_reference,
)
from koinoxrista.services import allocation_report, expense_report, receipt_report


def test_legacy_import_preserves_source_and_imports_all(app):
    source = Path(app.root_path).parent / "koinoxrista.db"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        building = Building(
            name="Legacy",
            address="Test 1",
            postal_code="00000",
            created_by_id=admin.id,
        )
        db.session.add(building)
        db.session.flush()
        result = import_legacy(building, source)
        db.session.commit()

        assert result["apartments"] == 6
        assert result["categories"] == 4
        assert db.session.scalar(db.select(db.func.count(Period.id))) == 143
        assert db.session.scalar(db.select(db.func.max(Period.id))) == 148
        assert db.session.scalar(db.select(db.func.min(Period.id))) == 1
        assert db.session.scalar(db.select(db.func.count(Expense.id))) == 753
        assert db.session.scalar(db.select(db.func.count(Allocation.id))) == 143 * 6 * 4
        managers = db.session.scalars(
            db.select(User).where(User.email.like("legacy-manager-%@invalid.local"))
        ).all()
        assert {manager.display_name for manager in managers} == {
            "Λάζαρος Θεόδωρος",
            "Μάρδα Βιβή",
        }
        assert all(manager.must_change_password for manager in managers)
        assert db.session.scalar(db.select(db.func.count(ManagerTerm.user_id))) == 2
        assert (
            db.session.scalar(
                db.select(db.func.count(BuildingMembership.id)).where(
                    BuildingMembership.role == "building_admin"
                )
            )
            == 2
        )
        first_period = db.session.scalar(db.select(Period).order_by(Period.issue_date))
        assert period_reference(first_period) == (
            "Περίοδος: Κανονικά και αγορά πετρελαίου 15/3/2008 (No: 1)"
        )
        report = allocation_report(first_period)
        assert len(report["categories"]) == 4
        assert len(report["rows"]) == 6
        assert report["millesimal_totals"] == [1000, 1000, 1000, 1000]
        assert report["has_expenses"] == [True, True, True, False]
        assert all(len(row["millesimals"]) == 4 for row in report["rows"])
        assert sum((row["total"] for row in report["rows"]), start=0) == first_period.total
        assert report["grand_total"] == first_period.total
        ledger = expense_report(first_period)
        assert len(ledger["categories"]) == 4
        assert ledger["has_expenses"] == [True, True, True, False]
        assert len(ledger["rows"]) == len(first_period.expenses)
        assert ledger["grand_total"] == first_period.total
        assert sum(ledger["totals"], start=0) == first_period.total
        receipts = receipt_report(first_period)
        expected_receipts = len(
            {
                allocation.apartment_id
                for allocation in first_period.allocations
                if sum(
                    (
                        item.amount
                        for item in first_period.allocations
                        if item.apartment_id == allocation.apartment_id
                    ),
                    start=0,
                )
                != 0
            }
        )
        assert len(receipts) == expected_receipts
        assert all(receipt["payable"] != 0 for receipt in receipts)
        assert sum((receipt["payable"] for receipt in receipts), start=0) == first_period.total
        period_pdf = create_period_report(first_period)
        receipts_pdf = create_receipts_report(first_period)
        assert period_pdf.startswith(b"%PDF")
        assert receipts_pdf.startswith(b"%PDF")
        assert len(period_pdf) > 5_000
        assert len(receipts_pdf) > 5_000
        new_period = Period(
            building=building,
            issue_date=date(2027, 1, 31),
            comments="Νέα περίοδος",
        )
        db.session.add(new_period)
        db.session.flush()
        assert new_period.id == 149
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert after == before

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from test_cash import add_entry, apartments, login, make_building

from koinoxrista.data_migrations import NEW_CASH_KINDS, OLD_CASH_KINDS, allow_credit_refunds
from koinoxrista.extensions import db
from koinoxrista.models import Building, CashEntry
from koinoxrista.services import apartment_position, cash_report

D = Decimal


def table_sql():
    return db.session.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cash_entries'")
    ).scalar()


def test_migration_rebuilds_cash_entries_keeping_rows(app):
    building_id = make_building(app)
    with app.app_context():
        # Recreate the table as older databases have it: without 'refund' in the CHECK.
        old_sql = table_sql().replace(NEW_CASH_KINDS, OLD_CASH_KINDS)
        old_indexes = (
            db.session.execute(
                text(
                    "SELECT sql FROM sqlite_master"
                    " WHERE type = 'index' AND tbl_name = 'cash_entries' AND sql IS NOT NULL"
                )
            )
            .scalars()
            .all()
        )
        assert old_indexes
        db.session.execute(text("DROP TABLE cash_entries"))
        db.session.execute(text(old_sql))
        for statement in old_indexes:
            db.session.execute(text(statement))
        db.session.commit()
        first = apartments(building_id)[0]
        add_entry(building_id, first, "collection", date(2026, 1, 5), "10.00", 1)

        assert allow_credit_refunds() is not None  # backup path
        assert NEW_CASH_KINDS in table_sql()
        indexes = db.session.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'cash_entries'"
            )
        ).scalars()
        assert {"ix_cash_entries_building_id", "ix_cash_entries_apartment_id"} <= set(indexes)
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        first = apartments(building_id)[0]
        assert apartment_position(first)[0] == D("0.00")  # the receipt survived
        add_entry(building_id, first, "refund", date(2026, 1, 6), "1.00", 2)  # now allowed
        assert allow_credit_refunds() is None  # idempotent


def test_credit_is_refunded_in_cash(app, client):
    building_id = make_building(app)
    with app.app_context():
        building = db.session.get(Building, building_id)
        building.opening_cash = D("50.00")
        second = apartments(building_id)[1]
        second.opening_balance = D("-30.00")  # Α2 paid 30 too much
        db.session.commit()
        second_id = second.id
    login(client)

    page = client.get(f"/buildings/{building_id}?tab=cash").text
    assert f"apartment_id={second_id}&amp;kind=refund" in page
    form = client.get(f"/buildings/{building_id}/cash/new?apartment_id={second_id}&kind=refund")
    assert 'value="refund" selected' in form.text and 'value="30.00"' in form.text

    def refund(amount):
        return client.post(
            f"/buildings/{building_id}/cash/new",
            data={
                "apartment_id": second_id,
                "kind": "refund",
                "date": "2026-01-10",
                "amount": amount,
            },
        )

    assert "ξεπερνά την πίστωση" in refund("30.01").text
    assert refund("20").status_code == 302
    with app.app_context():
        building = db.session.get(Building, building_id)
        assert apartment_position(apartments(building_id)[1])[0] == D("-10.00")
        assert cash_report(building)["cash"] == D("30.00")
        entry_id = db.session.scalar(db.select(CashEntry.id))
    pdf = client.get(f"/cash-entries/{entry_id}/receipt.pdf")
    assert pdf.mimetype == "application/pdf"
    ledger = client.get(f"/apartments/{second_id}/ledger").text
    assert "Επιστροφή πίστωσης" in ledger

    client.post(f"/cash-entries/{entry_id}/void", data={"reason": "Λάθος"})
    with app.app_context():
        assert apartment_position(apartments(building_id)[1])[0] == D("-30.00")

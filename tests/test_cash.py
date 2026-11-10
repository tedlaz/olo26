from datetime import date
from decimal import Decimal

from koinoxrista.extensions import db
from koinoxrista.models import (
    Apartment,
    Building,
    BuildingMembership,
    CashEntry,
    Expense,
    ExpenseCategory,
    ManagerTerm,
    Millesimal,
    Period,
    User,
)
from koinoxrista.pdf_reports import create_cash_receipt, create_payment_notices
from koinoxrista.services import (
    apartment_ledger,
    apartment_position,
    build_allocations,
    ledger_summary,
    previous_balance,
)

D = Decimal


def make_building(app):
    """Two apartments (600/400 millesimals), one category, a manager, ledger from 1/1/2026."""
    with app.app_context():
        admin = db.session.scalar(db.select(User))
        admin.must_change_password = False
        building = Building(
            name="Κτίριο",
            address="Οδός 1",
            postal_code="11111",
            created_by_id=admin.id,
            ledger_start_date=date(2026, 1, 1),
        )
        category = ExpenseCategory(building=building, name="Καθαριότητα")
        first = Apartment(building=building, name="Α1", number=1, opening_balance=D("10.00"))
        second = Apartment(building=building, name="Α2", number=2, opening_reserve=D("50.00"))
        db.session.add_all(
            [
                building,
                Millesimal(apartment=first, category=category, value=600),
                Millesimal(apartment=second, category=category, value=400),
            ]
        )
        db.session.flush()
        db.session.add(
            ManagerTerm(
                building_id=building.id, display_name="Διαχειριστής", date_from=date(2020, 1, 1)
            )
        )
        db.session.commit()
        return building.id


def finalize_period(building_id, issue_date, amount):
    building = db.session.get(Building, building_id)
    category = building.categories[0]
    period = Period(building=building, issue_date=issue_date, manager_name_snapshot="Δ")
    expense = Expense(
        period=period,
        category=category,
        invoice_date=issue_date,
        invoice_number=f"T-{issue_date}",
        description="Καθαρισμός",
        amount=D(amount),
    )
    db.session.add_all([period, expense])
    db.session.flush()
    build_allocations(period)
    db.session.commit()
    return period


def add_entry(building_id, apartment, kind, day, amount, number):
    entry = CashEntry(
        building_id=building_id,
        apartment=apartment,
        number=number,
        kind=kind,
        date=day,
        amount=D(amount),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def apartments(building_id):
    building = db.session.get(Building, building_id)
    return sorted(building.apartments, key=lambda item: item.number)


def test_running_balance_handles_late_partial_and_advance_payments(app):
    building_id = make_building(app)
    with app.app_context():
        first, second = apartments(building_id)
        # Before the ledger start: must not be charged (covered by the opening balance).
        finalize_period(building_id, date(2025, 12, 31), "100.00")
        january = finalize_period(building_id, date(2026, 1, 31), "100.00")  # 60 / 40
        assert apartment_position(first) == (D("70.00"), D("0"))
        assert apartment_position(second) == (D("40.00"), D("50.00"))

        # Α1 pays nothing in January, then pays two months at once plus an advance.
        february = finalize_period(building_id, date(2026, 2, 28), "50.00")  # 30 / 20
        add_entry(building_id, first, "collection", date(2026, 3, 5), "150.00", 1)
        assert apartment_position(first)[0] == D("-50.00")  # credit

        # Α2 pays part of January on the issue date: counts against that notice.
        add_entry(building_id, second, "collection", date(2026, 1, 31), "15.00", 2)
        assert previous_balance(second, january) == D("-15.00")
        assert previous_balance(second, february) == D("25.00")
        assert previous_balance(first, january) == D("10.00")
        assert previous_balance(first, february) == D("70.00")

        summary = ledger_summary(db.session.get(Building, building_id))
        assert summary["balance"] == D("-50.00") + D("45.00")
        assert summary["reserve"] == D("50.00")


def test_void_and_reserve_movements(app):
    building_id = make_building(app)
    with app.app_context():
        first, second = apartments(building_id)
        entry = add_entry(building_id, first, "collection", date(2026, 1, 5), "10.00", 1)
        add_entry(building_id, second, "reserve_in", date(2026, 1, 6), "20.00", 2)
        add_entry(building_id, second, "reserve_out", date(2026, 1, 7), "30.00", 3)
        assert apartment_position(first) == (D("0.00"), D("0"))
        assert apartment_position(second) == (D("0.00"), D("40.00"))

        entry.voided_at = date(2026, 1, 8)
        db.session.commit()
        rows = apartment_ledger(first)
        assert [row.entry for row in rows] == [entry]  # still listed
        assert apartment_position(first)[0] == D("10.00")  # but not counted


def test_notice_and_receipt_pdfs(app):
    building_id = make_building(app)
    with app.app_context():
        first, _second = apartments(building_id)
        period = finalize_period(building_id, date(2026, 1, 31), "100.00")
        entry = add_entry(building_id, first, "collection", date(2026, 2, 1), "70.00", 1)
        assert create_payment_notices(period).startswith(b"%PDF")
        assert create_cash_receipt(entry).startswith(b"%PDF")


def login(client):
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})


def test_cash_routes_record_print_and_void(app, client):
    building_id = make_building(app)
    with app.app_context():
        first_id = apartments(building_id)[0].id
    login(client)

    page = client.get(f"/buildings/{building_id}?tab=cash")
    assert page.status_code == 200 and "Νέα κίνηση" in page.text

    rejected = client.post(
        f"/buildings/{building_id}/cash/new",
        data={"apartment_id": first_id, "kind": "reserve_out", "date": "2026-02-01", "amount": "5"},
    )
    assert "ξεπερνά το αποθεματικό" in rejected.text

    response = client.post(
        f"/buildings/{building_id}/cash/new",
        data={"apartment_id": first_id, "kind": "collection", "date": "2026-02-01", "amount": "4"},
    )
    assert response.status_code == 302
    ledger = client.get(response.headers["Location"])
    assert "Εκτύπωση απόδειξης" in ledger.text
    with app.app_context():
        entry = db.session.scalar(db.select(CashEntry))
        assert (entry.number, entry.amount) == (1, D("4.00"))
        assert apartment_position(entry.apartment)[0] == D("6.00")
        entry_id = entry.id

    pdf = client.get(f"/cash-entries/{entry_id}/receipt.pdf")
    assert pdf.mimetype == "application/pdf"
    client.post(f"/cash-entries/{entry_id}/void", data={"reason": "Λάθος ποσό"})
    assert client.get(f"/cash-entries/{entry_id}/receipt.pdf").status_code == 200
    assert client.get(f"/buildings/{building_id}/cash/setup").status_code == 200
    assert (
        client.get(f"/buildings/{building_id}/cash/new?apartment_id={first_id}").status_code == 200
    )
    with app.app_context():
        entry = db.session.get(CashEntry, entry_id)
        assert entry.voided_at and entry.void_reason == "Λάθος ποσό"
        assert apartment_position(entry.apartment)[0] == D("10.00")


def test_cash_is_hidden_from_viewers(app, client):
    building_id = make_building(app)
    with app.app_context():
        viewer = User(email="viewer@example.com", display_name="Viewer")
        viewer.set_password("viewer-pass")
        db.session.add(viewer)
        db.session.flush()
        db.session.add(
            BuildingMembership(building_id=building_id, user_id=viewer.id, role="viewer")
        )
        db.session.commit()
        first_id = apartments(building_id)[0].id
    client.post("/auth/login", data={"email": "viewer@example.com", "password": "viewer-pass"})

    assert "Ταμείο" not in client.get(f"/buildings/{building_id}?tab=cash").text
    assert client.get(f"/apartments/{first_id}/ledger").status_code == 403
    assert client.get(f"/buildings/{building_id}/cash/new").status_code == 403


def test_cash_tab_order_and_only_nonzero_balances(app, client):
    building_id = make_building(app)  # Α1 owes 10, Α2 owes nothing
    with app.app_context():
        building = db.session.get(Building, building_id)
        building.apartments.append(
            Apartment(name="Α3", number=3, opening_balance=D("-5.00"))  # credit
        )
        db.session.commit()
    login(client)
    page = client.get(f"/buildings/{building_id}?tab=cash").text

    assert "<h2>Ανεξόφλητες δαπάνες</h2>" not in page  # none, so the section is hidden
    cash_at = page.index("Μετρητά στο ταμείο")
    balances_at = page.index("<h2>Υπόλοιπα διαμερισμάτων</h2>")
    book_at = page.index("<h2>Βιβλίο ταμείου")
    assert cash_at < balances_at < book_at
    balances = page[balances_at:book_at]
    assert "1. Α1" in balances and "3. Α3" in balances and "πίστωση" in balances
    assert "2. Α2" not in balances  # zero balance
    assert f"/buildings/{building_id}/cash/reserves" in page


def test_reserves_form_sets_opening_reserve_only(app, client):
    building_id = make_building(app)
    with app.app_context():
        first, second = apartments(building_id)
        add_entry(building_id, second, "reserve_out", date(2026, 1, 5), "30.00", 1)
        ids = first.id, second.id
    login(client)

    form = client.get(f"/buildings/{building_id}/cash/reserves").text
    assert "Τρέχον αποθεματικό" in form and "Οφειλή" not in form
    setup = client.get(f"/buildings/{building_id}/cash/setup").text
    assert "reserve_" not in setup

    too_low = client.post(
        f"/buildings/{building_id}/cash/reserves",
        data={f"reserve_{ids[0]}": "0", f"reserve_{ids[1]}": "20"},  # 20 - 30 refunded < 0
    )
    assert "θα γινόταν αρνητικό" in too_low.text
    client.post(
        f"/buildings/{building_id}/cash/reserves",
        data={f"reserve_{ids[0]}": "15", f"reserve_{ids[1]}": "40"},
    )
    with app.app_context():
        first, second = apartments(building_id)
        assert apartment_position(first) == (D("10.00"), D("15.00"))  # balance untouched
        assert apartment_position(second)[1] == D("10.00")

from datetime import date
from decimal import Decimal

from test_cash import add_entry, apartments, finalize_period, login, make_building

from koinoxrista.extensions import db
from koinoxrista.models import Building, CashEntry, Expense, ExpensePayment, Payee, Period
from koinoxrista.pdf_reports import amount_in_words, create_payment_receipt
from koinoxrista.services import (
    apartment_position,
    build_allocations,
    cash_book,
    cash_report,
    lowest_cash,
    payee_ledger,
)

D = Decimal


def test_amount_in_words():
    assert amount_in_words(D("120.50")) == "Εκατόν είκοσι ευρώ και πενήντα λεπτά"
    assert amount_in_words(D("1")) == "Ένα ευρώ"
    assert amount_in_words(D("0.01")) == "Ένα λεπτό"
    assert amount_in_words(D("214")) == "Διακόσια δεκατέσσερα ευρώ"
    assert amount_in_words(D("1234.56")) == (
        "Χίλια διακόσια τριάντα τέσσερα ευρώ και πενήντα έξι λεπτά"
    )
    assert amount_in_words(D("213000")) == "Διακόσιες δεκατρείς χιλιάδες ευρώ"
    assert amount_in_words(D("21000")) == "Είκοσι μία χιλιάδες ευρώ"
    assert amount_in_words(D("3004")) == "Τρεις χιλιάδες τέσσερα ευρώ"
    assert amount_in_words(D("2500000")) == "Δύο εκατομμύρια πεντακόσιες χιλιάδες ευρώ"


def pay(building, expense, day, amount, number):
    payment = ExpensePayment(
        building=building,
        expense=expense,
        number=number,
        date=day,
        amount=D(amount),
        payee=expense.payee.name,
    )
    db.session.add(payment)
    db.session.commit()
    return payment


def draft_with_bill(building, amount, issue=date(2026, 1, 31)):
    category = building.categories[0]
    payee = Payee(building=building, name=f"ΔΕΗ {issue}")
    draft = Period(building=building, issue_date=issue, manager_name_snapshot="Δ")
    bill = Expense(
        period=draft,
        category=category,
        payee=payee,
        invoice_date=date(issue.year, issue.month, 10),
        invoice_number=f"ΔΕΗ-{issue}",
        description="ΔΕΗ",
        amount=D(amount),
    )
    db.session.add_all([payee, draft, bill])
    db.session.commit()
    return draft, bill


def test_full_cycle_leaves_exactly_the_reserves_in_cash(app):
    building_id = make_building(app)  # Α1 owes 10, Α2 has 50 reserve
    with app.app_context():
        building = db.session.get(Building, building_id)
        building.opening_cash = D("40.00")  # = reserves 50 - debts 10
        db.session.commit()
        first, second = apartments(building_id)

        def check(cash):
            report = cash_report(building)
            assert report["cash"] == D(cash)
            assert report["difference"] == 0, report
            assert lowest_cash(building)[0] >= 0

        check("40.00")
        add_entry(building_id, first, "reserve_in", date(2026, 1, 2), "100.00", 1)
        check("140.00")

        # The manager pays a bill of the running (draft) period from the reserve, in two parts.
        draft, bill = draft_with_bill(building, "100.00")
        pay(building, bill, date(2026, 1, 12), "30.00", 1)
        pay(building, bill, date(2026, 1, 20), "70.00", 2)
        check("40.00")

        build_allocations(draft)  # issue: Α1 60, Α2 40
        db.session.commit()
        check("40.00")

        february = finalize_period(building_id, date(2026, 2, 28), "50.00")  # unpaid bill
        february.expenses[0].payee = Payee(building=building, name="Καθαρισμός")
        db.session.commit()
        check("40.00")
        assert cash_report(building)["unpaid"] == D("50.00")

        add_entry(building_id, first, "collection", date(2026, 3, 1), "100.00", 2)  # 10+60+30
        add_entry(building_id, second, "collection", date(2026, 3, 2), "60.00", 3)  # 40+20
        check("200.00")

        pay(building, february.expenses[0], date(2026, 3, 3), "50.00", 3)
        check("150.00")
        report = cash_report(building)
        assert report["cash"] == report["reserve"] == D("150.00")
        assert report["payable_expenses"] == []

        book = cash_book(building)
        assert [row.balance for row in book] == [
            D(x) for x in ("140", "110", "40", "140", "200", "150")
        ]


def test_payment_routes_guard_cash_and_print(app, client):
    building_id = make_building(app)
    with app.app_context():
        building = db.session.get(Building, building_id)
        building.opening_cash = D("60.00")
        _draft, bill = draft_with_bill(building, "80.00")
        expense_id, period_id = bill.id, bill.period_id
    login(client)

    assert "Πληρωμή" in client.get(f"/periods/{period_id}").text
    assert client.get(f"/expenses/{expense_id}/pay").status_code == 200
    too_much = client.post(
        f"/expenses/{expense_id}/pay", data={"date": "2026-01-15", "amount": "80.01"}
    )
    assert "ξεπερνά το υπόλοιπο" in too_much.text
    no_cash = client.post(
        f"/expenses/{expense_id}/pay", data={"date": "2026-01-15", "amount": "70"}
    )
    assert "Το ταμείο δεν επαρκεί" in no_cash.text
    response = client.post(
        f"/expenses/{expense_id}/pay",
        data={"date": "2026-01-15", "amount": "50", "notes": "1η δόση"},
    )
    assert response.status_code == 302
    assert "Εκτύπωση απόδειξης πληρωμής" in client.get(response.headers["Location"]).text
    period_page = client.get(f"/periods/{period_id}").text
    assert 'class="paid-icon partial"' in period_page
    assert 'title="Πληρωμένα 50.00 από 80.00 € · υπόλοιπο 30.00 €"' in period_page

    with app.app_context():
        payment = db.session.scalar(db.select(ExpensePayment))
        assert (payment.number, payment.amount, payment.payee) == (1, D("50.00"), "ΔΕΗ 2026-01-31")
        assert create_payment_receipt(payment).startswith(b"%PDF")
        payment_id = payment.id
        first_id = apartments(building_id)[0].id
    assert client.get(f"/expense-payments/{payment_id}/receipt.pdf").mimetype == "application/pdf"

    # A collection later than the payment does not help an earlier date.
    client.post(
        f"/buildings/{building_id}/cash/new",
        data={"apartment_id": first_id, "kind": "collection", "date": "2026-02-01", "amount": "30"},
    )
    backdated = client.post(
        f"/expenses/{expense_id}/pay", data={"date": "2026-01-16", "amount": "20"}
    )
    assert "θα γινόταν αρνητικό" in backdated.text
    assert (
        client.post(
            f"/expenses/{expense_id}/pay", data={"date": "2026-02-02", "amount": "20"}
        ).status_code
        == 302
    )

    # Cash is now 60 - 50 + 30 - 20 = 20: voiding the 30 receipt would make it negative.
    with app.app_context():
        receipt_id = db.session.scalar(db.select(CashEntry.id))
    client.post(f"/cash-entries/{receipt_id}/void", data={"reason": "Λάθος"})
    with app.app_context():
        assert db.session.get(CashEntry, receipt_id).voided_at is None

    # A paid expense can be neither deleted nor reduced below what was paid.
    client.post(f"/expenses/{expense_id}/delete")
    with app.app_context():
        payee_id = db.session.get(Expense, expense_id).payee_id
    lowered = client.post(
        f"/expenses/{expense_id}/edit",
        data={
            "invoice_date": "2026-01-10",
            "invoice_number": "Κ-7",
            "description": "ΔΕΗ",
            "category_id": "1",
            "amount": "40",
            "payee_id": str(payee_id),
        },
    )
    assert "μικρότερο από τα πληρωμένα" in lowered.text
    with app.app_context():
        assert db.session.get(Expense, expense_id).amount == D("80.00")

    # Opening cash cannot be lowered below what the book needs (the 50 paid on 15/01).
    setup = client.post(
        f"/buildings/{building_id}/cash/setup",
        data={"ledger_start_date": "2026-01-01", "opening_cash": "0"},
    )
    assert "Το ταμείο δεν επαρκεί" in setup.text
    with app.app_context():
        assert db.session.get(Building, building_id).opening_cash == D("60.00")

    client.post(f"/expense-payments/{payment_id}/void", data={"reason": "Διπλή καταχώρηση"})
    with app.app_context():
        assert db.session.get(Expense, expense_id).paid == D("20.00")
    assert client.get(f"/expense-payments/{payment_id}/receipt.pdf").status_code == 200
    page = client.get(f"/buildings/{building_id}?tab=cash").text
    assert "Βιβλίο ταμείου" in page and "Απόδειξη πληρωμής αρ. 2" in page


def test_payees_are_managed_per_building(app, client):
    building_id = make_building(app)
    login(client)
    client.post(f"/buildings/{building_id}/payees", data={"name": "ΕΥΔΑΠ"})
    duplicate = client.post(
        f"/buildings/{building_id}/payees", data={"name": "ΕΥΔΑΠ"}, follow_redirects=True
    )
    assert "Υπάρχει ήδη δικαιούχος" in duplicate.text
    with app.app_context():
        payee_id = db.session.scalar(db.select(Payee.id))
    client.post(f"/payees/{payee_id}/rename", data={"name": "ΕΥΔΑΠ Α.Ε."})
    page = client.get(f"/buildings/{building_id}?tab=payees").text
    assert "ΕΥΔΑΠ Α.Ε." in page


def test_expenses_of_periods_before_the_ledger_count_as_paid(app, client):
    building_id = make_building(app)  # ledger starts 01/01/2026
    with app.app_context():
        old = finalize_period(building_id, date(2025, 12, 31), "100.00")
        expense_id, period_id = old.expenses[0].id, old.id
        report = cash_report(db.session.get(Building, building_id))
        assert report["unpaid"] == 0 and report["payable_expenses"] == []
    login(client)

    page = client.get(f"/periods/{period_id}").text
    assert "Εξοφλημένη" in page and f"/expenses/{expense_id}/pay" not in page
    assert client.get(f"/expenses/{expense_id}/pay").status_code == 409


def test_new_expense_with_typed_payee_and_cash_payment(app, client):
    building_id = make_building(app)
    with app.app_context():
        building = db.session.get(Building, building_id)
        building.opening_cash = D("50.00")
        db.session.add(Payee(building=building, name="Ευδαπ"))
        period = Period(building=building, issue_date=date(2026, 1, 31), manager_name_snapshot="Δ")
        db.session.add(period)
        db.session.commit()
        period_id, category_id = period.id, building.categories[0].id
    login(client)
    assert "Πληρώθηκε μετρητοίς" in client.get(f"/periods/{period_id}").text

    def add(number, payee, amount, cash):
        data = {
            "invoice_date": "2026-01-10",
            "invoice_number": number,
            "description": "Δαπάνη",
            "category_id": str(category_id),
            "amount": amount,
            "payee_name": payee,
        }
        if cash:
            data["paid_cash"] = "1"
        return client.post(f"/periods/{period_id}/expenses", data=data)

    add("Α-1", "ΕΥΔΑΠ", "20", cash=False)  # same payee, other case
    response = add("Α-2", "  Ηλεκτρολόγος   Παπάς ", "30", cash=True)  # new payee, paid now
    assert "new_payment=" in response.headers["Location"]
    page = client.get(response.headers["Location"]).text
    assert "Εκτύπωση απόδειξης πληρωμής" in page
    assert 'class="paid-icon"' in page
    assert 'title="Πληρωμένη: 30.00 € · τελευταία πληρωμή 10/01/2026"' in page
    rejected = add("Α-3", "Ηλεκτρολόγος Παπάς", "25", cash=True)  # cash left: 20
    assert "new_payment=" not in rejected.headers["Location"]

    with app.app_context():
        names = sorted(payee.name for payee in db.session.scalars(db.select(Payee)))
        assert names == ["Ευδαπ", "Ηλεκτρολόγος Παπάς"]
        expenses = {e.invoice_number: e for e in db.session.scalars(db.select(Expense))}
        assert set(expenses) == {"Α-1", "Α-2"}  # the rejected one was not kept
        assert expenses["Α-1"].payee.name == "Ευδαπ" and expenses["Α-1"].paid == 0
        payment = expenses["Α-2"].payments[0]
        assert (payment.amount, payment.date, payment.payee) == (
            D("30.00"),
            date(2026, 1, 10),
            "Ηλεκτρολόγος Παπάς",
        )
        assert cash_report(db.session.get(Building, building_id))["cash"] == D("20.00")


def test_apartment_pays_an_expense_directly(app, client):
    building_id = make_building(app)  # Α1 owes 10, Α2 reserve 50
    with app.app_context():
        building = db.session.get(Building, building_id)
        building.opening_cash = D("40.00")  # reconciled: 50 - 10
        _draft, bill = draft_with_bill(building, "80.00")
        expense_id = bill.id
        first_id = apartments(building_id)[0].id
    login(client)

    response = client.post(
        f"/expenses/{expense_id}/pay",
        data={"date": "2026-01-15", "amount": "80", "paid_by": str(first_id)},
    )
    assert response.status_code == 302  # allowed although the cash (40) is less than 80
    with app.app_context():
        building = db.session.get(Building, building_id)
        first = apartments(building_id)[0]
        payment = db.session.scalar(db.select(ExpensePayment))
        entry = payment.cash_entry
        assert (entry.kind, entry.amount, entry.apartment_id) == (
            "collection",
            D("80.00"),
            first_id,
        )
        assert apartment_position(first)[0] == D("-70.00")  # 10 owed - 80 paid = credit 70
        report = cash_report(building)
        assert report["cash"] == D("40.00") and report["difference"] == 0
        assert db.session.get(Expense, expense_id).paid == D("80.00")
        payment_id, entry_id = payment.id, entry.id

    # Voiding either half voids both.
    client.post(f"/cash-entries/{entry_id}/void", data={"reason": "Λάθος"})
    with app.app_context():
        assert db.session.get(ExpensePayment, payment_id).voided_at is not None
        assert db.session.get(Expense, expense_id).paid == 0
        assert apartment_position(apartments(building_id)[0])[0] == D("10.00")


def test_payee_card_shows_expenses_payments_and_balance(app, client):
    building_id = make_building(app)
    with app.app_context():
        building = db.session.get(Building, building_id)
        building.opening_cash = D("40.00")
        _draft, bill = draft_with_bill(building, "100.00")
        payee = bill.payee
        old = finalize_period(building_id, date(2025, 12, 31), "70.00")  # before the ledger
        old.expenses[0].payee = payee
        db.session.commit()
        pay(building, bill, date(2026, 1, 12), "30.00", 1)
        voided = pay(building, bill, date(2026, 1, 13), "5.00", 2)
        voided.voided_at, voided.void_reason = date(2026, 1, 14), "Λάθος"
        db.session.commit()
        pay(building, bill, date(2026, 1, 20), "10.00", 3)
        rows = payee_ledger(payee)
        assert [row.balance for row in rows] == [D(x) for x in ("100", "70", "70", "60")]
        payee_id = payee.id
    login(client)

    card = client.get(f"/payees/{payee_id}/ledger")
    assert card.status_code == 200
    assert "Απόδειξη πληρωμής αρ. 3" in card.text and "Ακυρωμένη: Λάθος" in card.text
    assert "Περίοδος No 2" not in card.text  # the old period's expense is not listed
    assert "Πίσω στο Κτίριο" in card.text
    payees = client.get(f"/buildings/{building_id}?tab=payees").text
    assert f"/payees/{payee_id}/ledger" in payees and "60.00 €" in payees


def test_payee_card_lists_newest_first(app, client):
    building_id = make_building(app)
    with app.app_context():
        building = db.session.get(Building, building_id)
        building.opening_cash = D("40.00")
        _draft, bill = draft_with_bill(building, "100.00")
        pay(building, bill, date(2026, 1, 12), "10.00", 1)
        pay(building, bill, date(2026, 1, 20), "10.00", 2)
        payee_id = bill.payee_id
    login(client)
    card = client.get(f"/payees/{payee_id}/ledger").text
    newer, older, expense = (
        card.index(x) for x in ("Απόδειξη πληρωμής αρ. 2", "Απόδειξη πληρωμής αρ. 1", "παρ/κό")
    )
    assert newer < older < expense

from datetime import date
from decimal import Decimal

from koinoxrista.extensions import db
from koinoxrista.models import Building, Expense, ExpenseCategory, Payee, Period, User


def create_expense(app, *, period_status="draft"):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        building = Building(
            name="Κτίριο δοκιμής",
            address="Δοκιμής 1",
            postal_code="11111",
            created_by_id=admin.id,
        )
        category = ExpenseCategory(building=building, name="Καθαρισμός")
        payee = Payee(building=building, name="Συνεργείο")
        period = Period(
            building=building,
            issue_date=date(2026, 8, 1),
            manager_name_snapshot="Διαχειριστής",
            status=period_status,
        )
        expense = Expense(
            period=period,
            category=category,
            invoice_date=date(2026, 8, 2),
            invoice_number="A-1",
            description="Αρχική περιγραφή",
            amount=Decimal("10.00"),
            payee=payee,
        )
        db.session.add(building)
        db.session.commit()
        return period.id, expense.id, category.id


def login(client):
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})


def test_period_expenses_show_distinct_create_panel_and_edit_action(app, client):
    period_id, expense_id, _ = create_expense(app)
    login(client)

    response = client.get(f"/periods/{period_id}")

    assert response.status_code == 200
    assert "Νέα δαπάνη" in response.text
    assert "Ημερομηνία παραστατικού" in response.text
    assert f"/expenses/{expense_id}/edit" in response.text
    assert "Διόρθωση" in response.text
    assert 'class="expense-action-badge icon-badge danger" title="Διαγραφή"' in response.text
    assert 'data-confirm="Να διαγραφεί οριστικά αυτή η δαπάνη;"' in response.text


def test_expense_can_be_edited(app, client):
    period_id, expense_id, category_id = create_expense(app)
    with app.app_context():
        payee_id = db.session.scalar(db.select(Payee.id))
    login(client)
    form = client.get(f"/expenses/{expense_id}/edit").text
    assert 'list="payee-list"' in form and 'value="Συνεργείο" required' in form

    response = client.post(
        f"/expenses/{expense_id}/edit",
        data={
            "invoice_date": "2026-08-03",
            "invoice_number": "B-2",
            "description": "Νέα περιγραφή",
            "category_id": str(category_id),
            "amount": "25.40",
            "payee_id": str(payee_id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/periods/{period_id}")
    with app.app_context():
        expense = db.session.get(Expense, expense_id)
        assert expense.invoice_date == date(2026, 8, 3)
        assert expense.invoice_number == "B-2"
        assert expense.description == "Νέα περιγραφή"
        assert expense.amount == Decimal("25.40")


def test_expense_edit_rejects_category_from_another_building(app, client):
    _, expense_id, category_id = create_expense(app)
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        other_building = Building(
            name="Άλλο κτίριο",
            address="Άλλη 1",
            postal_code="22222",
            created_by_id=admin.id,
        )
        other_category = ExpenseCategory(building=other_building, name="Θέρμανση")
        db.session.add(other_building)
        db.session.commit()
        other_category_id = other_category.id
    login(client)

    response = client.post(
        f"/expenses/{expense_id}/edit",
        data={
            "invoice_date": "2026-08-03",
            "invoice_number": "B-2",
            "description": "Νέα περιγραφή",
            "category_id": str(other_category_id),
            "amount": "25.40",
        },
    )

    assert response.status_code == 200
    assert "Μη έγκυρη κατηγορία" in response.text
    with app.app_context():
        expense = db.session.get(Expense, expense_id)
        assert expense.category_id == category_id
        assert expense.description == "Αρχική περιγραφή"


def test_finalized_expense_cannot_be_edited(app, client):
    _, expense_id, category_id = create_expense(app, period_status="finalized")
    login(client)

    get_response = client.get(f"/expenses/{expense_id}/edit")
    post_response = client.post(
        f"/expenses/{expense_id}/edit",
        data={
            "invoice_date": "2026-08-03",
            "invoice_number": "B-2",
            "description": "Νέα περιγραφή",
            "category_id": str(category_id),
            "amount": "25.40",
        },
    )

    assert get_response.status_code == 409
    assert post_response.status_code == 409


def test_draft_period_shows_provisional_allocation_without_saving_it(app, client):
    from koinoxrista.models import Allocation, Apartment, Millesimal

    period_id, _, category_id = create_expense(app)
    login(client)

    # incomplete matrix: the page explains why there is no allocation yet
    response = client.get(f"/periods/{period_id}")
    assert "Κατανομή ανά διαμέρισμα" in response.text
    assert "Δεν μπορεί να υπολογιστεί η κατανομή" in response.text

    with app.app_context():
        category = db.session.get(ExpenseCategory, category_id)
        for number, value in ((1, 600), (2, 400)):
            apartment = Apartment(
                building_id=category.building_id, name=f"Δ{number}", number=number
            )
            db.session.add(Millesimal(apartment=apartment, category=category, value=value))
        db.session.commit()

    response = client.get(f"/periods/{period_id}")
    assert "Πρόχειρη · με τα τρέχοντα χιλιοστά" in response.text
    assert "6.00 €" in response.text and "4.00 €" in response.text
    assert "600‰</small>" in response.text and "400‰</small>" in response.text
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Allocation.id))) == 0


def test_dashboard_card_shows_latest_finalized_and_drafts(app, client):
    period_id, _, _ = create_expense(app)
    with app.app_context():
        draft = db.session.get(Period, period_id)
        db.session.add(
            Period(
                building_id=draft.building_id,
                issue_date=date(2026, 7, 1),
                manager_name_snapshot="Δ",
                status="finalized",
            )
        )
        db.session.commit()
    login(client)

    page = client.get("/").text
    assert "Τελευταία οριστική" in page and "01/07/2026" in page
    assert "Πρόχειρες" in page and "01/08/2026 · 10.00 €" in page


def test_period_pdfs_open_inline_in_browser(app, client):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        building = Building(name="Κ", address="Ο 1", postal_code="1", created_by_id=admin.id)
        period = Period(building=building, issue_date=date(2026, 8, 1), status="finalized")
        db.session.add(period)
        db.session.commit()
        period_id = period.id
    login(client)

    for path, name in (
        ("report.pdf", "koinoxrista"),
        ("notices.pdf", "eidopoiiseis"),
        ("receipts.pdf", "apodeixeis"),
    ):
        response = client.get(f"/periods/{period_id}/{path}")
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert response.headers["Content-Disposition"] == f"inline; filename={name}-2026-08-01.pdf"


def test_new_expense_requires_a_payee_of_the_building(app, client):
    period_id, _, category_id = create_expense(app)
    login(client)
    data = {
        "invoice_date": "2026-08-05",
        "invoice_number": "N-1",
        "description": "Λάμπες",
        "category_id": str(category_id),
        "amount": "12",
    }
    response = client.post(f"/periods/{period_id}/expenses", data=data, follow_redirects=True)
    assert "Επιλέξτε ή γράψτε δικαιούχο" in response.text
    with app.app_context():
        payee_id = db.session.scalar(db.select(Payee.id))
    client.post(
        f"/periods/{period_id}/expenses",
        data=data | {"payee_id": str(payee_id), "recurring": "1"},
    )
    with app.app_context():
        expense = db.session.scalar(db.select(Expense).where(Expense.invoice_number == "N-1"))
        assert expense.payee.name == "Συνεργείο" and expense.recurring

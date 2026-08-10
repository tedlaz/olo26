from datetime import date
from decimal import Decimal

from koinoxrista.extensions import db
from koinoxrista.models import Building, Expense, ExpenseCategory, Period, User


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
    assert f'/expenses/{expense_id}/edit' in response.text
    assert "Διόρθωση" in response.text
    assert 'class="expense-action-badge danger"' in response.text
    assert 'data-confirm="Να διαγραφεί οριστικά αυτή η δαπάνη;"' in response.text


def test_expense_can_be_edited(app, client):
    period_id, expense_id, category_id = create_expense(app)
    login(client)

    response = client.post(
        f"/expenses/{expense_id}/edit",
        data={
            "invoice_date": "2026-08-03",
            "invoice_number": "B-2",
            "description": "Νέα περιγραφή",
            "category_id": str(category_id),
            "amount": "25.40",
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

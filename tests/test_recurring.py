from datetime import date
from decimal import Decimal

from test_cash import login, make_building

from koinoxrista.extensions import db
from koinoxrista.models import Building, Expense, Payee, Period
from koinoxrista.services import _shift_description, recurring_suggestions, shift_months

D = Decimal


def test_shift_months_and_description():
    assert shift_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert shift_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert _shift_description(
        "Καθαριότητα Σεπτέμβριος 2026", date(2026, 9, 30), date(2026, 10, 30)
    ) == ("Καθαριότητα Οκτώβριος 2026")
    assert _shift_description(
        "ΚΑΘΑΡΙΟΤΗΤΑ ΔΕΚΕΜΒΡΙΟΣ 2026", date(2026, 12, 31), date(2027, 1, 31)
    ) == ("ΚΑΘΑΡΙΟΤΗΤΑ ΙΑΝΟΥΑΡΙΟΣ 2027")
    assert _shift_description("Ασανσέρ", date(2026, 9, 1), date(2026, 10, 1)) == "Ασανσέρ"


def test_recurring_expenses_are_suggested_and_added(app, client):
    building_id = make_building(app)
    with app.app_context():
        building = db.session.get(Building, building_id)
        category = building.categories[0]
        cleaner = Payee(building=building, name="Συνεργείο")
        september = Period(building=building, issue_date=date(2026, 9, 30), status="finalized")
        db.session.add_all(
            [
                cleaner,
                september,
                Expense(
                    period=september,
                    category=category,
                    payee=cleaner,
                    recurring=True,
                    invoice_date=date(2026, 9, 30),
                    invoice_number="ΑΠΟΔ.",
                    description="Καθαριότητα Σεπτέμβριος 2026",
                    amount=D("50.00"),
                ),
                Expense(
                    period=september,
                    category=category,
                    payee=cleaner,
                    invoice_date=date(2026, 9, 12),
                    invoice_number="Λ-1",
                    description="Λάμπες",
                    amount=D("12.00"),
                ),
            ]
        )
        october = Period(building=building, issue_date=date(2026, 10, 31))
        db.session.add(october)
        db.session.commit()
        october_id, september_id = october.id, september.id
        _source, items = recurring_suggestions(october)
        assert [item.description for item in items] == ["Καθαριότητα Οκτώβριος 2026"]
        source_id = items[0].source.id
    login(client)

    page = client.get(f"/periods/{october_id}").text
    assert "Επαναλαμβανόμενες δαπάνες" in page and "Λάμπες" not in page
    assert f"/buildings/{building_id}?tab=cash" in page  # link to the cash book
    client.post(
        f"/periods/{october_id}/recurring",
        data={
            "copy": str(source_id),
            f"date_{source_id}": "2026-10-31",
            f"number_{source_id}": "ΑΠΟΔ.",
            f"description_{source_id}": "Καθαριότητα Οκτώβριος 2026",
            f"amount_{source_id}": "55",  # the price went up
        },
    )
    with app.app_context():
        added = db.session.scalar(db.select(Expense).where(Expense.period_id == october_id))
        assert (added.amount, added.payee.name, added.recurring) == (D("55.00"), "Συνεργείο", True)
        assert added.invoice_date == date(2026, 10, 31)
        _source, items = recurring_suggestions(db.session.get(Period, october_id))
        assert items == []  # already added, not suggested again
    assert "Επαναλαμβανόμενες δαπάνες" not in client.get(f"/periods/{october_id}").text
    assert "Επαναλαμβανόμενες δαπάνες" not in client.get(f"/periods/{september_id}").text

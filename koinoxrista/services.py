import calendar
import unicodedata
from collections import defaultdict
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from types import SimpleNamespace

from sqlalchemy import or_

from .extensions import db
from .models import (
    Allocation,
    Apartment,
    AuditLog,
    Building,
    BuildingMembership,
    CashEntry,
    Expense,
    ExpenseCategory,
    ExpensePayment,
    ManagerTerm,
    Millesimal,
    Payee,
    Period,
    utcnow,
)

GREEK_MONTHS = [
    "Ιανουάριος",
    "Φεβρουάριος",
    "Μάρτιος",
    "Απρίλιος",
    "Μάιος",
    "Ιούνιος",
    "Ιούλιος",
    "Αύγουστος",
    "Σεπτέμβριος",
    "Οκτώβριος",
    "Νοέμβριος",
    "Δεκέμβριος",
]


class BuildingWizardError(ValueError):
    def __init__(self, message, step):
        super().__init__(message)
        self.step = step


def _wizard_text(values, key):
    value = values.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def audit(action, user_id=None, building_id=None, details=""):
    db.session.add(
        AuditLog(
            user_id=user_id,
            building_id=building_id,
            action=action,
            details=details,
        )
    )


def create_building_graph(payload, creator_id):
    if not isinstance(payload, dict):
        raise BuildingWizardError("Τα δεδομένα της δημιουργίας δεν είναι έγκυρα.", 1)

    details = payload.get("building")
    if not isinstance(details, dict):
        raise BuildingWizardError("Συμπληρώστε τα στοιχεία του κτιρίου.", 1)
    name = _wizard_text(details, "name")
    address = _wizard_text(details, "address")
    postal_code = _wizard_text(details, "postal_code")
    if not all((name, address, postal_code)):
        raise BuildingWizardError("Όλα τα στοιχεία του κτιρίου είναι υποχρεωτικά.", 1)
    if len(name) > 100 or len(address) > 200 or len(postal_code) > 10:
        raise BuildingWizardError("Κάποιο στοιχείο του κτιρίου υπερβαίνει το επιτρεπτό μήκος.", 1)

    apartment_rows = payload.get("apartments")
    if not isinstance(apartment_rows, list) or not apartment_rows:
        raise BuildingWizardError("Προσθέστε τουλάχιστον ένα διαμέρισμα.", 2)
    apartments = []
    apartment_keys = set()
    apartment_numbers = set()
    try:
        for row in apartment_rows:
            if not isinstance(row, dict):
                raise ValueError
            key = _wizard_text(row, "key")
            apartment_name = _wizard_text(row, "name")
            number = int(row.get("number"))
            floor = int(row.get("floor") or 0)
            square_meters = Decimal(str(row.get("square_meters", 0)))
            if not key or not apartment_name:
                raise BuildingWizardError("Η ονομασία κάθε διαμερίσματος είναι υποχρεωτική.", 2)
            if key in apartment_keys or number in apartment_numbers:
                raise BuildingWizardError(
                    "Οι αριθμοί των διαμερισμάτων πρέπει να είναι μοναδικοί.", 2
                )
            if len(apartment_name) > 100:
                raise BuildingWizardError(
                    "Η ονομασία διαμερίσματος υπερβαίνει το επιτρεπτό μήκος.", 2
                )
            if not square_meters.is_finite() or square_meters < 0:
                raise BuildingWizardError(
                    "Τα τετραγωνικά μέτρα πρέπει να είναι μη αρνητικός αριθμός.", 2
                )
            owner = _wizard_text(row, "owner")
            occupant = _wizard_text(row, "occupant")
            if len(owner) > 100 or len(occupant) > 100:
                raise BuildingWizardError(
                    "Τα στοιχεία ιδιοκτήτη ή ενοίκου υπερβαίνουν το επιτρεπτό μήκος.", 2
                )
            apartment_keys.add(key)
            apartment_numbers.add(number)
            apartments.append(
                (
                    key,
                    Apartment(
                        name=apartment_name,
                        number=number,
                        floor=floor,
                        square_meters=square_meters,
                        owner=owner,
                        occupant=occupant,
                    ),
                )
            )
    except (TypeError, ValueError, InvalidOperation) as exc:
        if isinstance(exc, BuildingWizardError):
            raise
        raise BuildingWizardError(
            "Ο αριθμός, ο όροφος και τα τετραγωνικά κάθε διαμερίσματος πρέπει να είναι έγκυροι.",
            2,
        ) from exc

    category_rows = payload.get("categories")
    if not isinstance(category_rows, list) or not category_rows:
        raise BuildingWizardError("Προσθέστε τουλάχιστον έναν τύπο δαπάνης.", 3)
    categories = []
    category_keys = set()
    category_names = set()
    for row in category_rows:
        if not isinstance(row, dict):
            raise BuildingWizardError("Τα στοιχεία των τύπων δαπανών δεν είναι έγκυρα.", 3)
        key = _wizard_text(row, "key")
        category_name = _wizard_text(row, "name")
        if not key or not category_name:
            raise BuildingWizardError("Η ονομασία κάθε τύπου δαπάνης είναι υποχρεωτική.", 3)
        normalized_name = category_name.casefold()
        if key in category_keys or normalized_name in category_names:
            raise BuildingWizardError("Οι τύποι δαπανών πρέπει να έχουν μοναδικές ονομασίες.", 3)
        if len(category_name) > 100:
            raise BuildingWizardError("Η ονομασία τύπου δαπάνης υπερβαίνει το επιτρεπτό μήκος.", 3)
        category_keys.add(key)
        category_names.add(normalized_name)
        categories.append((key, ExpenseCategory(name=category_name)))

    matrix = payload.get("millesimals")
    if not isinstance(matrix, dict):
        raise BuildingWizardError("Συμπληρώστε τα χιλιοστά των τύπων δαπανών.", 3)
    expected_apartment_keys = {key for key, _item in apartments}
    if set(matrix) != category_keys:
        raise BuildingWizardError("Τα χιλιοστά δεν αντιστοιχούν στους τύπους δαπανών.", 3)
    raw_values = {}
    for category_key, _category in categories:
        column = matrix.get(category_key)
        if not isinstance(column, dict) or set(column) != expected_apartment_keys:
            raise BuildingWizardError("Τα χιλιοστά δεν αντιστοιχούν στα διαμερίσματα.", 3)
        for apartment_key, _apartment in apartments:
            raw_values[(apartment_key, category_key)] = column[apartment_key]

    building = Building(
        name=name,
        address=address,
        postal_code=postal_code,
        created_by_id=creator_id,
    )
    db.session.add(building)
    for _key, apartment in apartments:
        apartment.building = building
    for _key, category in categories:
        category.building = building
    db.session.add_all([item for _key, item in apartments])
    db.session.add_all([item for _key, item in categories])
    db.session.flush()

    values = {
        (apartment.id, category.id): raw_values[(apartment_key, category_key)]
        for apartment_key, apartment in apartments
        for category_key, category in categories
    }
    try:
        normalized_values = validate_millesimal_values(
            [item for _key, item in apartments],
            [item for _key, item in categories],
            values,
        )
    except ValueError as exc:
        raise BuildingWizardError(str(exc), 3) from exc

    db.session.add(BuildingMembership(building=building, user_id=creator_id, role="building_admin"))
    db.session.add_all(
        Millesimal(apartment_id=apartment_id, category_id=category_id, value=value)
        for (apartment_id, category_id), value in normalized_values.items()
    )
    audit("building_created", creator_id, building.id)
    return building


def validate_millesimal_values(apartments, categories, values):
    if not apartments or not categories:
        raise ValueError("Απαιτείται τουλάχιστον ένα διαμέρισμα και μία κατηγορία.")
    normalized = {}
    for category in categories:
        total = 0
        for apartment in apartments:
            key = (apartment.id, category.id)
            try:
                raw_value = values.get(key, 0)
                value = int(raw_value)
                if isinstance(raw_value, bool) or Decimal(str(raw_value)) != value:
                    raise ValueError
            except (TypeError, ValueError, InvalidOperation) as exc:
                raise ValueError("Τα χιλιοστά πρέπει να είναι ακέραιοι αριθμοί.") from exc
            if not 0 <= value <= 1000:
                raise ValueError("Κάθε τιμή χιλιοστών πρέπει να είναι από 0 έως 1000.")
            normalized[key] = value
            total += value
        if total != 1000:
            raise ValueError(f"Η κατηγορία «{category.name}» έχει {total} αντί για 1000 χιλιοστά.")
    return normalized


def replace_millesimals(apartments, categories, values):
    normalized = validate_millesimal_values(apartments, categories, values)
    apartment_ids = [item.id for item in apartments]
    category_ids = [item.id for item in categories]
    existing = {
        (item.apartment_id, item.category_id): item
        for item in db.session.scalars(
            db.select(Millesimal).where(
                Millesimal.apartment_id.in_(apartment_ids),
                Millesimal.category_id.in_(category_ids),
            )
        )
    }
    for key, value in normalized.items():
        item = existing.get(key)
        if item:
            item.value = value
        else:
            db.session.add(Millesimal(apartment_id=key[0], category_id=key[1], value=value))


def allocate_amount(total, shares):
    """Return exact cent allocations using deterministic largest remainder."""
    total_cents = int((Decimal(total) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    sign = -1 if total_cents < 0 else 1
    absolute = abs(total_cents)
    rows = []
    allocated = 0
    for apartment_id, millesimals in shares:
        exact = Decimal(absolute * millesimals) / Decimal(1000)
        base = int(exact.to_integral_value(rounding=ROUND_FLOOR))
        rows.append([apartment_id, base, exact - base])
        allocated += base
    remainder = absolute - allocated
    rows.sort(key=lambda row: (-row[2], row[0]))
    for index in range(remainder):
        rows[index][1] += 1
    return {row[0]: Decimal(sign * row[1]) / Decimal(100) for row in rows}


def delete_building(building):
    """Delete a building and everything that belongs to it.

    Foreign keys are enforced, so children go first. Audit entries are kept
    (detached from the building) so the history of who did what survives.
    """
    building_id = building.id
    period_ids = db.select(Period.id).where(Period.building_id == building_id)
    apartment_ids = db.select(Apartment.id).where(Apartment.building_id == building_id)
    db.session.expunge(building)
    for statement in (
        db.delete(CashEntry).where(CashEntry.building_id == building_id),
        db.delete(ExpensePayment).where(ExpensePayment.building_id == building_id),
        db.delete(Allocation).where(Allocation.period_id.in_(period_ids)),
        db.delete(Expense).where(Expense.period_id.in_(period_ids)),
        db.delete(Payee).where(Payee.building_id == building_id),
        db.delete(Period).where(Period.building_id == building_id),
        db.delete(Millesimal).where(Millesimal.apartment_id.in_(apartment_ids)),
        db.delete(Apartment).where(Apartment.building_id == building_id),
        db.delete(ExpenseCategory).where(ExpenseCategory.building_id == building_id),
        db.delete(ManagerTerm).where(ManagerTerm.building_id == building_id),
        db.delete(BuildingMembership).where(BuildingMembership.building_id == building_id),
        db.update(AuditLog).where(AuditLog.building_id == building_id).values(building_id=None),
        db.delete(Building).where(Building.id == building_id),
    ):
        db.session.execute(statement, execution_options={"synchronize_session": False})


def compute_allocations(period):
    """Split the period's expenses by the current millesimal matrix, without saving anything."""
    apartments = sorted(period.building.apartments, key=lambda item: (item.number, item.id))
    categories = sorted(period.building.categories, key=lambda item: item.id)
    matrix = {
        (item.apartment_id, item.category_id): item.value
        for item in db.session.scalars(
            db.select(Millesimal)
            .join(Millesimal.apartment)
            .where(Millesimal.apartment.has(building_id=period.building_id))
        )
    }
    validate_millesimal_values(
        apartments,
        categories,
        {(a.id, c.id): matrix.get((a.id, c.id), 0) for a in apartments for c in categories},
    )
    totals = defaultdict(lambda: Decimal("0"))
    for expense in period.expenses:
        totals[expense.category_id] += expense.amount
    result = []
    for category in categories:
        category_total = totals[category.id]
        shares = [(apartment.id, matrix[(apartment.id, category.id)]) for apartment in apartments]
        amounts = allocate_amount(category_total, shares)
        for apartment in apartments:
            result.append(
                SimpleNamespace(
                    apartment_id=apartment.id,
                    category_id=category.id,
                    apartment=apartment,
                    category=category,
                    millesimals=matrix[(apartment.id, category.id)],
                    category_total=category_total,
                    amount=amounts[apartment.id],
                )
            )
    return result


def build_allocations(period, reconstructed=False):
    db.session.query(Allocation).filter_by(period_id=period.id).delete()
    for item in compute_allocations(period):
        db.session.add(
            Allocation(
                period=period,
                apartment=item.apartment,
                category=item.category,
                apartment_name=item.apartment.display_label(),
                category_name=item.category.name,
                millesimals=item.millesimals,
                category_total=item.category_total,
                amount=item.amount,
                reconstructed=reconstructed,
            )
        )
    period.status = "finalized"
    period.finalized_at = utcnow()


def allocation_report(period, allocations=None):
    """Build the apartment/category matrix and its exact financial totals.

    Uses the stored (finalized) allocations unless `allocations` is given,
    e.g. a draft preview from compute_allocations().
    """
    amounts = defaultdict(lambda: Decimal("0"))
    millesimals = {}
    apartments = {}
    categories = {}
    for allocation in period.allocations if allocations is None else allocations:
        amounts[(allocation.apartment_id, allocation.category_id)] += allocation.amount
        millesimals[(allocation.apartment_id, allocation.category_id)] = allocation.millesimals
        apartments[allocation.apartment_id] = allocation.apartment
        categories[allocation.category_id] = allocation.category

    ordered_apartments = sorted(apartments.values(), key=lambda item: (item.number, item.id))
    ordered_categories = sorted(categories.values(), key=lambda item: (item.id, item.name))
    expense_category_ids = {expense.category_id for expense in period.expenses}
    has_expenses = [category.id in expense_category_ids for category in ordered_categories]
    rows = []
    category_totals = {category.id: Decimal("0") for category in ordered_categories}
    for apartment in ordered_apartments:
        values = []
        row_millesimals = []
        row_total = Decimal("0")
        for category in ordered_categories:
            amount = amounts[(apartment.id, category.id)]
            values.append(amount)
            row_millesimals.append(millesimals[(apartment.id, category.id)])
            row_total += amount
            category_totals[category.id] += amount
        rows.append(
            {
                "label": apartment.display_label(period.building.apartment_display_mode),
                "values": values,
                "millesimals": row_millesimals,
                "total": row_total,
            }
        )

    totals = [category_totals[category.id] for category in ordered_categories]
    millesimal_totals = [
        sum(millesimals[(apartment.id, category.id)] for apartment in ordered_apartments)
        for category in ordered_categories
    ]
    return {
        "categories": ordered_categories,
        "rows": rows,
        "totals": totals,
        "millesimal_totals": millesimal_totals,
        "has_expenses": has_expenses,
        "grand_total": sum(totals, Decimal("0")),
    }


def expense_report(period):
    """Build an expense ledger with one amount column per category."""
    categories = sorted(period.building.categories, key=lambda item: (item.id, item.name))
    category_indexes = {category.id: index for index, category in enumerate(categories)}
    expense_category_ids = {expense.category_id for expense in period.expenses}
    totals = [Decimal("0") for _category in categories]
    rows = []
    for expense in sorted(period.expenses, key=lambda item: (item.invoice_date, item.id)):
        values = [None for _category in categories]
        index = category_indexes[expense.category_id]
        values[index] = expense.amount
        totals[index] += expense.amount
        rows.append({"expense": expense, "values": values})
    return {
        "categories": categories,
        "rows": rows,
        "totals": totals,
        "has_expenses": [category.id in expense_category_ids for category in categories],
        "grand_total": sum(totals, Decimal("0")),
    }


def receipt_report(period):
    """Build per-apartment receipt rows reconciled to finalized allocations."""
    allocations = {(item.apartment_id, item.category_id): item for item in period.allocations}
    apartments = {item.apartment_id: item.apartment for item in period.allocations}
    expenses_by_category = defaultdict(list)
    for expense in sorted(period.expenses, key=lambda item: (item.invoice_date, item.id)):
        expenses_by_category[expense.category_id].append(expense)

    row_amounts = defaultdict(lambda: Decimal("0"))
    for category_id, expenses in expenses_by_category.items():
        category_allocations = [
            allocation
            for (apartment_id, stored_category_id), allocation in allocations.items()
            if stored_category_id == category_id
        ]
        shares = sorted(
            (
                (allocation.apartment_id, allocation.millesimals)
                for allocation in category_allocations
            ),
            key=lambda item: item[0],
        )
        for expense in expenses:
            for apartment_id, amount in allocate_amount(expense.amount, shares).items():
                row_amounts[(apartment_id, expense.id)] = amount

        last_expense = expenses[-1]
        for allocation in category_allocations:
            current_total = sum(
                (row_amounts[(allocation.apartment_id, expense.id)] for expense in expenses),
                Decimal("0"),
            )
            row_amounts[(allocation.apartment_id, last_expense.id)] += (
                allocation.amount - current_total
            )

    receipts = []
    ordered_expenses = sorted(period.expenses, key=lambda item: (item.invoice_date, item.id))
    for apartment in sorted(apartments.values(), key=lambda item: (item.number, item.id)):
        rows = []
        for expense in ordered_expenses:
            allocation = allocations[(apartment.id, expense.category_id)]
            rows.append(
                {
                    "expense": expense,
                    "millesimals": allocation.millesimals,
                    "amount": row_amounts[(apartment.id, expense.id)],
                }
            )
        payable = sum((row["amount"] for row in rows), Decimal("0"))
        stored_payable = sum(
            (
                allocation.amount
                for (apartment_id, _category_id), allocation in allocations.items()
                if apartment_id == apartment.id
            ),
            Decimal("0"),
        )
        if payable != stored_payable:
            raise ValueError("Η απόδειξη δεν συμφωνεί με την οριστική κατανομή.")
        if payable == Decimal("0"):
            continue
        receipts.append({"apartment": apartment, "rows": rows, "payable": payable})
    return receipts


ZERO = Decimal("0.00")


def _period_charges(apartment):
    """{period: amount} for finalized periods issued on/after the building's ledger start."""
    start = apartment.building.ledger_start_date
    if start is None:
        return {}
    charges = defaultdict(lambda: ZERO)
    allocations = db.session.scalars(
        db.select(Allocation)
        .join(Allocation.period)
        .where(
            Allocation.apartment_id == apartment.id,
            Period.status == "finalized",
            Period.issue_date >= start,
        )
    )
    for allocation in allocations:
        charges[allocation.period] += allocation.amount
    return charges


def apartment_ledger(apartment):
    """Chronological apartment card with running balance (positive = owes) and reserve.

    On the same day a period's charge comes before the cash entries, so a payment made on
    the issue date settles that period. Voided entries are listed but not counted.
    """
    rows = [
        SimpleNamespace(date=period.issue_date, period=period, entry=None, charge=amount)
        for period, amount in _period_charges(apartment).items()
    ]
    rows += [
        SimpleNamespace(date=entry.date, period=None, entry=entry, charge=ZERO)
        for entry in db.session.scalars(
            db.select(CashEntry).where(CashEntry.apartment_id == apartment.id)
        )
    ]
    rows.sort(key=lambda row: (row.date, row.entry is not None, row.entry.id if row.entry else 0))
    balance = Decimal(apartment.opening_balance)
    reserve = Decimal(apartment.opening_reserve)
    for row in rows:
        entry = row.entry
        row.collected = row.reserve_change = ZERO
        if entry and not entry.voided_at:
            if entry.kind == "collection":
                row.collected = entry.amount
            else:
                row.reserve_change = entry.amount if entry.kind == "reserve_in" else -entry.amount
        balance += row.charge - row.collected
        reserve += row.reserve_change
        row.balance, row.reserve = balance, reserve
    return rows


def apartment_position(apartment):
    """Current (balance, reserve) of an apartment."""
    rows = apartment_ledger(apartment)
    if rows:
        return rows[-1].balance, rows[-1].reserve
    return Decimal(apartment.opening_balance), Decimal(apartment.opening_reserve)


def previous_balance(apartment, period):
    """Balance before this period's charge, counting payments up to its issue date.

    None when the period is outside the cash ledger (not started, or issued earlier).
    """
    start = apartment.building.ledger_start_date
    if start is None or period.issue_date < start:
        return None
    balance = Decimal(apartment.opening_balance)
    for row in apartment_ledger(apartment):
        if row.period is not None and row.date < period.issue_date:
            balance += row.charge
        elif row.entry is not None and row.date <= period.issue_date:
            balance -= row.collected
    return balance


def ledger_summary(building):
    """Per-apartment balance and reserve, with totals."""
    rows = []
    for apartment in sorted(building.apartments, key=lambda item: (item.number, item.id)):
        balance, reserve = apartment_position(apartment)
        rows.append(SimpleNamespace(apartment=apartment, balance=balance, reserve=reserve))
    return {
        "rows": rows,
        "balance": sum((row.balance for row in rows), ZERO),
        "reserve": sum((row.reserve for row in rows), ZERO),
    }


def _next_number(model, building):
    current = db.session.scalar(
        db.select(db.func.max(model.number)).where(model.building_id == building.id)
    )
    return (current or 0) + 1


def next_cash_entry_number(building):
    return _next_number(CashEntry, building)


def next_payment_number(building):
    return _next_number(ExpensePayment, building)


def period_in_ledger(period):
    """Expenses payable through the cash ledger: draft period, or issued on/after its start."""
    start = period.building.ledger_start_date
    return start is not None and (period.status == "draft" or period.issue_date >= start)


def expense_in_ledger(expense):
    return period_in_ledger(expense.period)


def cash_report(building):
    """Cash on hand from the movements, and what it should be (the reconciliation).

    expected = reserves - apartment debts + unpaid expenses of issued periods
               - payments already made for expenses of not-yet-issued (draft) periods
    When everybody has paid and every expense is settled, cash == reserves.
    """
    cash = Decimal(building.opening_cash) + sum(
        (row.delta for row in _cash_movements(building)), ZERO
    )

    start = building.ledger_start_date
    expenses = []
    if start is not None:
        expenses = db.session.scalars(
            db.select(Expense)
            .join(Expense.period)
            .where(
                Period.building_id == building.id,
                or_(Period.status == "draft", Period.issue_date >= start),
            )
            .order_by(Expense.invoice_date, Expense.id)
        ).all()
    unpaid = sum((e.amount - e.paid for e in expenses if e.period.status == "finalized"), ZERO)
    prepaid = sum((e.paid for e in expenses if e.period.status == "draft"), ZERO)
    summary = ledger_summary(building)
    expected = summary["reserve"] - summary["balance"] + unpaid - prepaid
    return {
        "cash": cash,
        "reserve": summary["reserve"],
        "debts": summary["balance"],
        "unpaid": unpaid,
        "prepaid": prepaid,
        "expected": expected,
        "difference": cash - expected,
        "payable_expenses": [e for e in expenses if e.amount - e.paid > 0],
    }


def manager_on(building_id, day):
    """The building's manager term active on that day, or None."""
    return db.session.scalar(
        db.select(ManagerTerm).where(
            ManagerTerm.building_id == building_id,
            ManagerTerm.date_from <= day,
            or_(ManagerTerm.date_to.is_(None), ManagerTerm.date_to >= day),
        )
    )


def _cash_movements(building, include_voided=False):
    """Every cash movement of the building, in book order: by date, inflows before outflows."""
    rows = []
    entries = db.session.scalars(db.select(CashEntry).where(CashEntry.building_id == building.id))
    for entry in entries:
        sign = -1 if entry.kind == "reserve_out" else 1
        rows.append(
            SimpleNamespace(date=entry.date, delta=sign * entry.amount, entry=entry, payment=None)
        )
    payments = db.session.scalars(
        db.select(ExpensePayment).where(ExpensePayment.building_id == building.id)
    )
    for payment in payments:
        rows.append(
            SimpleNamespace(date=payment.date, delta=-payment.amount, entry=None, payment=payment)
        )
    if not include_voided:
        rows = [row for row in rows if not (row.entry or row.payment).voided_at]
    rows.sort(
        key=lambda row: (
            row.date,
            row.delta < 0,
            row.entry.number if row.entry else row.payment.number,
        )
    )
    return rows


def cash_book(building):
    """Receipts and payments in one book with running balance; voided rows shown, not counted."""
    balance = Decimal(building.opening_cash)
    rows = _cash_movements(building, include_voided=True)
    for row in rows:
        if not (row.entry or row.payment).voided_at:
            balance += row.delta
        row.balance = balance
    return rows


def lowest_cash(building, add=None, skip=None):
    """(lowest balance, date) the cash would reach, optionally with an extra movement
    add=(date, delta) or without an existing one (skip=entry or payment)."""
    rows = [
        (row.date, row.delta)
        for row in _cash_movements(building)
        if skip is None or (row.entry or row.payment) is not skip
    ]
    if add is not None:
        rows.append(add)
    rows.sort(key=lambda row: (row[0], row[1] < 0))
    balance = lowest = Decimal(building.opening_cash)
    lowest_date = building.ledger_start_date
    for day, delta in rows:
        balance += delta
        if balance < lowest:
            lowest, lowest_date = balance, day
    return lowest, lowest_date


def require_cash(building, add=None, skip=None):
    """Raise ValueError if the cash would ever go negative."""
    lowest, day = lowest_cash(building, add=add, skip=skip)
    if lowest < 0:
        when = f" στις {day:%d/%m/%Y}" if day else ""
        raise ValueError(f"Το ταμείο δεν επαρκεί: θα γινόταν αρνητικό ({lowest} €){when}.")


def payee_ledger(payee):
    """Payee card: its expenses (owed) and the payments to it, with running balance.

    Expenses of periods issued before the ledger start count as paid and are left out.
    """
    expenses = db.session.scalars(db.select(Expense).where(Expense.payee_id == payee.id)).all()
    rows = []
    for expense in expenses:
        if not expense_in_ledger(expense):
            continue
        rows.append(
            SimpleNamespace(
                date=expense.invoice_date, expense=expense, payment=None, charge=expense.amount
            )
        )
        rows += [
            SimpleNamespace(date=payment.date, expense=expense, payment=payment, charge=ZERO)
            for payment in expense.payments
        ]
    rows.sort(
        key=lambda row: (
            row.date,
            row.payment is not None,
            row.payment.number if row.payment else row.expense.id,
        )
    )
    balance = ZERO
    for row in rows:
        payment = row.payment
        row.paid = payment.amount if payment and not payment.voided_at else ZERO
        balance += row.charge - row.paid
        row.balance = balance
    return rows


def payee_balance(payee):
    rows = payee_ledger(payee)
    return rows[-1].balance if rows else ZERO


def shift_months(day, months):
    """Same day `months` later, clamped to the month's last day (31/01 + 1 -> 28/02)."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return day.replace(
        year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1])
    )


def _plain_upper(text):
    """'Σεπτέμβριος' -> 'ΣΕΠΤΕΜΒΡΙΟΣ' (how older descriptions are written)."""
    decomposed = unicodedata.normalize("NFD", text.upper())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _shift_description(text, source, target):
    """Move a month/year mentioned in the description to the target date's month/year."""
    for transform in (str, _plain_upper):
        old, new = (
            transform(GREEK_MONTHS[source.month - 1]),
            transform(GREEK_MONTHS[target.month - 1]),
        )
        text = text.replace(f"{old} {source.year}", f"{new} {target.year}").replace(old, new)
    return text


def recurring_suggestions(period):
    """Recurring expenses of the building's previous period that has any, moved to this period.

    Ones whose category and payee already have a recurring expense here are skipped.
    """
    source = db.session.scalar(
        db.select(Period)
        .join(Period.expenses)
        .where(
            Period.building_id == period.building_id,
            Period.issue_date < period.issue_date,
            Expense.recurring.is_(True),
        )
        .order_by(Period.issue_date.desc())
        .limit(1)
    )
    if source is None:
        return None, []
    months = (period.issue_date.year - source.issue_date.year) * 12 + (
        period.issue_date.month - source.issue_date.month
    )
    present = {(e.category_id, e.payee_id) for e in period.expenses if e.recurring}
    suggestions = []
    for expense in sorted(source.expenses, key=lambda item: (item.invoice_date, item.id)):
        if not expense.recurring or (expense.category_id, expense.payee_id) in present:
            continue
        invoice_date = shift_months(expense.invoice_date, months)
        suggestions.append(
            SimpleNamespace(
                source=expense,
                invoice_date=invoice_date,
                invoice_number=expense.invoice_number,
                description=_shift_description(
                    expense.description, expense.invoice_date, invoice_date
                ),
                amount=expense.amount,
            )
        )
    return source, suggestions

from collections import defaultdict
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from .extensions import db
from .models import Allocation, AuditLog, Millesimal, utcnow


def audit(action, user_id=None, building_id=None, details=""):
    db.session.add(
        AuditLog(
            user_id=user_id,
            building_id=building_id,
            action=action,
            details=details,
        )
    )


def validate_millesimal_values(apartments, categories, values):
    if not apartments or not categories:
        raise ValueError("Απαιτείται τουλάχιστον ένα διαμέρισμα και μία κατηγορία.")
    normalized = {}
    for category in categories:
        total = 0
        for apartment in apartments:
            key = (apartment.id, category.id)
            try:
                value = int(values.get(key, 0))
            except (TypeError, ValueError) as exc:
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


def build_allocations(period, reconstructed=False):
    db.session.query(Allocation).filter_by(period_id=period.id).delete()
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
    for category in categories:
        category_total = totals[category.id]
        shares = [(apartment.id, matrix[(apartment.id, category.id)]) for apartment in apartments]
        amounts = allocate_amount(category_total, shares)
        for apartment in apartments:
            db.session.add(
                Allocation(
                    period=period,
                    apartment=apartment,
                    category=category,
                    apartment_name=apartment.display_label(),
                    category_name=category.name,
                    millesimals=matrix[(apartment.id, category.id)],
                    category_total=category_total,
                    amount=amounts[apartment.id],
                    reconstructed=reconstructed,
                )
            )
    period.status = "finalized"
    period.finalized_at = utcnow()


def allocation_report(period):
    """Build the apartment/category matrix and its exact financial totals."""
    amounts = defaultdict(lambda: Decimal("0"))
    millesimals = {}
    apartments = {}
    categories = {}
    for allocation in period.allocations:
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

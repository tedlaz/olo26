from datetime import date, datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint, event, func, select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    display_name = db.Column(db.String(100), nullable=False, default="")
    password_hash = db.Column(db.String(512), nullable=False)
    is_system_admin = db.Column(db.Boolean, nullable=False, default=False)
    can_create_building = db.Column(db.Boolean, nullable=False, default=False)
    is_active_account = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    theme = db.Column(db.String(20), nullable=False, default="light", server_default="light")
    auth_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    memberships = db.relationship("BuildingMembership", back_populates="user")

    @property
    def is_active(self):
        return self.is_active_account

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return f"{self.id}:{self.auth_version}"


class Invitation(db.Model):
    __tablename__ = "invitations"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    email = db.Column(db.String(254))
    created_by_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    user_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    created_by_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)

    user = db.relationship("User", foreign_keys=[user_id])


class Building(db.Model):
    __tablename__ = "buildings"
    __table_args__ = (
        CheckConstraint(
            "apartment_display_mode IN ('name', 'owner', 'occupant')",
            name="valid_apartment_display_mode",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(200), nullable=False)
    postal_code = db.Column(db.String(10), nullable=False)
    apartment_display_mode = db.Column(
        db.String(20), nullable=False, default="occupant", server_default="occupant"
    )
    created_by_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    legacy_imported_at = db.Column(db.DateTime)
    legacy_source_hash = db.Column(db.String(64), unique=True)
    # Cash ledger starts here; earlier periods are covered by the apartments' opening balances.
    ledger_start_date = db.Column(db.Date)
    opening_cash = db.Column(db.Numeric(12, 2), nullable=False, default=0, server_default="0")

    apartments = db.relationship(
        "Apartment", back_populates="building", cascade="all, delete-orphan"
    )
    categories = db.relationship(
        "ExpenseCategory", back_populates="building", cascade="all, delete-orphan"
    )
    periods = db.relationship("Period", back_populates="building", cascade="all, delete-orphan")


class BuildingMembership(db.Model):
    __tablename__ = "building_memberships"
    __table_args__ = (
        UniqueConstraint("building_id", "user_id"),
        CheckConstraint("role IN ('viewer', 'editor', 'building_admin')", name="valid_role"),
    )

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.ForeignKey("buildings.id"), nullable=False, index=True)
    user_id = db.Column(db.ForeignKey("users.id"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)

    building = db.relationship("Building")
    user = db.relationship("User", back_populates="memberships")


class ManagerTerm(db.Model):
    __tablename__ = "manager_terms"
    __table_args__ = (
        CheckConstraint("date_to IS NULL OR date_to >= date_from", name="valid_dates"),
    )

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.ForeignKey("buildings.id"), nullable=False, index=True)
    user_id = db.Column(db.ForeignKey("users.id"), index=True)
    display_name = db.Column(db.String(100), nullable=False)
    date_from = db.Column(db.Date, nullable=False)
    date_to = db.Column(db.Date)
    legacy_id = db.Column(db.Integer)

    user = db.relationship("User")


class Apartment(db.Model):
    __tablename__ = "apartments"
    __table_args__ = (UniqueConstraint("building_id", "number"),)

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.ForeignKey("buildings.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    number = db.Column(db.Integer, nullable=False)
    floor = db.Column(db.Integer, nullable=False, default=0)
    square_meters = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    owner = db.Column(db.String(100), nullable=False, default="")
    occupant = db.Column(db.String(100), nullable=False, default="")
    legacy_id = db.Column(db.Integer)
    # Balances on Building.ledger_start_date. Positive balance = the apartment owes.
    opening_balance = db.Column(db.Numeric(12, 2), nullable=False, default=0, server_default="0")
    opening_reserve = db.Column(db.Numeric(12, 2), nullable=False, default=0, server_default="0")

    building = db.relationship("Building", back_populates="apartments")

    def display_label(self, mode=None):
        mode = mode or self.building.apartment_display_mode
        value = {
            "name": self.name,
            "owner": self.owner,
            "occupant": self.occupant,
        }.get(mode, self.name)
        value = (value or self.name).strip()
        return f"{self.number}. {value}"


class ExpenseCategory(db.Model):
    __tablename__ = "expense_categories"
    __table_args__ = (UniqueConstraint("building_id", "name"),)

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.ForeignKey("buildings.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    legacy_id = db.Column(db.Integer)

    building = db.relationship("Building", back_populates="categories")


class Payee(db.Model):
    """Who gets paid for an expense (supplier, utility, worker), per building."""

    __tablename__ = "payees"
    __table_args__ = (UniqueConstraint("building_id", "name"),)

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.ForeignKey("buildings.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)

    building = db.relationship("Building")


class Millesimal(db.Model):
    __tablename__ = "millesimals"
    __table_args__ = (
        UniqueConstraint("apartment_id", "category_id"),
        CheckConstraint("value >= 0 AND value <= 1000", name="valid_value"),
    )

    id = db.Column(db.Integer, primary_key=True)
    apartment_id = db.Column(db.ForeignKey("apartments.id"), nullable=False, index=True)
    category_id = db.Column(db.ForeignKey("expense_categories.id"), nullable=False, index=True)
    value = db.Column(db.Integer, nullable=False)

    apartment = db.relationship("Apartment")
    category = db.relationship("ExpenseCategory")


class Period(db.Model):
    __tablename__ = "periods"
    __table_args__ = (
        UniqueConstraint("building_id", "issue_date"),
        UniqueConstraint("building_id", "number", name="uq_periods_building_number"),
        CheckConstraint("status IN ('draft', 'finalized')", name="valid_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.ForeignKey("buildings.id"), nullable=False, index=True)
    # "No" shown to users: 1, 2, 3, … within each building (assigned on insert, see below)
    number = db.Column(db.Integer)
    issue_date = db.Column(db.Date, nullable=False)
    comments = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="draft")
    manager_term_id = db.Column(db.ForeignKey("manager_terms.id"))
    manager_name_snapshot = db.Column(db.String(100), nullable=False, default="")
    finalized_at = db.Column(db.DateTime)
    reopened_count = db.Column(db.Integer, nullable=False, default=0)

    building = db.relationship("Building", back_populates="periods")
    manager_term = db.relationship("ManagerTerm")
    expenses = db.relationship("Expense", back_populates="period", cascade="all, delete-orphan")
    allocations = db.relationship(
        "Allocation", back_populates="period", cascade="all, delete-orphan"
    )

    @property
    def total(self):
        return sum((expense.amount for expense in self.expenses), Decimal("0"))


class Expense(db.Model):
    __tablename__ = "expenses"
    __table_args__ = (UniqueConstraint("period_id", "invoice_date", "invoice_number"),)

    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.ForeignKey("periods.id"), nullable=False, index=True)
    category_id = db.Column(db.ForeignKey("expense_categories.id"), nullable=False, index=True)
    invoice_date = db.Column(db.Date, nullable=False)
    invoice_number = db.Column(db.String(30), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    payee_id = db.Column(db.ForeignKey("payees.id"), index=True)
    # Suggested again in the next period (monthly cleaning, elevator service, ...).
    recurring = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    legacy_id = db.Column(db.Integer)

    period = db.relationship("Period", back_populates="expenses")
    category = db.relationship("ExpenseCategory")
    payee = db.relationship("Payee")
    payments = db.relationship("ExpensePayment", back_populates="expense")

    @property
    def paid(self):
        return sum(
            (payment.amount for payment in self.payments if not payment.voided_at), Decimal("0")
        )


class Allocation(db.Model):
    __tablename__ = "allocations"
    __table_args__ = (UniqueConstraint("period_id", "apartment_id", "category_id"),)

    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.ForeignKey("periods.id"), nullable=False, index=True)
    apartment_id = db.Column(db.ForeignKey("apartments.id"), nullable=False)
    category_id = db.Column(db.ForeignKey("expense_categories.id"), nullable=False)
    apartment_name = db.Column(db.String(100), nullable=False)
    category_name = db.Column(db.String(100), nullable=False)
    millesimals = db.Column(db.Integer, nullable=False)
    category_total = db.Column(db.Numeric(12, 2), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    reconstructed = db.Column(db.Boolean, nullable=False, default=False)

    period = db.relationship("Period", back_populates="allocations")
    apartment = db.relationship("Apartment")
    category = db.relationship("ExpenseCategory")


class CashEntry(db.Model):
    """A numbered cash movement of an apartment. Voided, never deleted."""

    __tablename__ = "cash_entries"
    __table_args__ = (
        UniqueConstraint("building_id", "number", name="uq_cash_entries_building_number"),
        CheckConstraint(
            "kind IN ('collection', 'reserve_in', 'reserve_out', 'refund')",
            name="valid_cash_entry_kind",
        ),
        CheckConstraint("amount > 0", name="positive_cash_entry_amount"),
    )

    KINDS = {
        "collection": "Είσπραξη κοινοχρήστων",
        "reserve_in": "Κατάθεση αποθεματικού",
        "reserve_out": "Επιστροφή αποθεματικού",
        "refund": "Επιστροφή πίστωσης",
    }
    OUTFLOWS = ("reserve_out", "refund")

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.ForeignKey("buildings.id"), nullable=False, index=True)
    apartment_id = db.Column(db.ForeignKey("apartments.id"), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    party_name = db.Column(db.String(100), nullable=False, default="")
    notes = db.Column(db.String(200), nullable=False, default="")
    created_by_id = db.Column(db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    voided_at = db.Column(db.DateTime)
    void_reason = db.Column(db.String(200), nullable=False, default="")

    building = db.relationship("Building")
    apartment = db.relationship("Apartment")

    @property
    def kind_label(self):
        return self.KINDS[self.kind]


class ExpensePayment(db.Model):
    """A numbered (own series) payment of an expense, possibly one of several instalments."""

    __tablename__ = "expense_payments"
    __table_args__ = (
        UniqueConstraint("building_id", "number", name="uq_expense_payments_building_number"),
        CheckConstraint("amount > 0", name="positive_expense_payment_amount"),
    )

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.ForeignKey("buildings.id"), nullable=False, index=True)
    expense_id = db.Column(db.ForeignKey("expenses.id"), nullable=False, index=True)
    # Set when an apartment paid the payee directly: the receipt that credits the apartment.
    cash_entry_id = db.Column(db.ForeignKey("cash_entries.id"))
    number = db.Column(db.Integer, nullable=False)
    date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    payee = db.Column(db.String(100), nullable=False, default="")
    notes = db.Column(db.String(200), nullable=False, default="")
    created_by_id = db.Column(db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    voided_at = db.Column(db.DateTime)
    void_reason = db.Column(db.String(200), nullable=False, default="")

    building = db.relationship("Building")
    expense = db.relationship("Expense", back_populates="payments")
    cash_entry = db.relationship("CashEntry", backref=db.backref("covered_payment", uselist=False))


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("users.id"))
    building_id = db.Column(db.ForeignKey("buildings.id"), index=True)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


@event.listens_for(Session, "before_flush")
def number_new_periods(session, _flush_context, _instances):
    """Give every new period the next number of its building, whatever code created it.

    ponytail: max()+1 per flush; two admins creating a period in the same building at the
    same instant would hit the unique constraint (the save fails, nothing is corrupted).
    """
    pending = [item for item in session.new if isinstance(item, Period) and item.number is None]
    next_number = {}
    # explicit ids first (the legacy import keeps the old system's order), then by date
    for period in sorted(
        pending, key=lambda p: (p.id is None, p.id or 0, p.issue_date or date.min)
    ):
        building = period.building
        building_id = (
            period.building_id if period.building_id is not None else getattr(building, "id", None)
        )
        key = building_id if building_id is not None else id(building)
        if key not in next_number:
            current = 0
            if building_id is not None:
                with session.no_autoflush:
                    current = (
                        session.scalar(
                            select(func.max(Period.number)).where(Period.building_id == building_id)
                        )
                        or 0
                    )
            next_number[key] = current
        next_number[key] += 1
        period.number = next_number[key]

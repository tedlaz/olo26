from datetime import datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint
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
        CheckConstraint("status IN ('draft', 'finalized')", name="valid_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    building_id = db.Column(db.ForeignKey("buildings.id"), nullable=False, index=True)
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
    legacy_id = db.Column(db.Integer)

    period = db.relationship("Period", back_populates="expenses")
    category = db.relationship("ExpenseCategory")


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


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("users.id"))
    building_id = db.Column(db.ForeignKey("buildings.id"), index=True)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

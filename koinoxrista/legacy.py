import hashlib
import secrets
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from .extensions import db
from .models import (
    Apartment,
    Building,
    BuildingMembership,
    Expense,
    ExpenseCategory,
    ManagerTerm,
    Millesimal,
    Period,
    User,
    utcnow,
)
from .services import build_allocations

EXPECTED_TABLES = {
    "koi_category",
    "koi_dapanes",
    "koi_diamerisma",
    "koi_diaxeiristis",
    "koi_koinoxrista",
    "koi_xiliosta",
}


def materialize_legacy_managers(building_id=None):
    """Create application users for imported manager terms that do not have one."""
    query = db.select(ManagerTerm).where(
        ManagerTerm.user_id.is_(None), ManagerTerm.legacy_id.is_not(None)
    )
    if building_id is not None:
        query = query.where(ManagerTerm.building_id == building_id)

    created = 0
    for term in db.session.scalars(query):
        email = f"legacy-manager-{term.building_id}-{term.legacy_id}@invalid.local"
        user = db.session.scalar(db.select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                display_name=term.display_name,
                must_change_password=True,
            )
            user.set_password(secrets.token_urlsafe(32))
            db.session.add(user)
            db.session.flush()
            created += 1
        membership = db.session.scalar(
            db.select(BuildingMembership).where(
                BuildingMembership.building_id == term.building_id,
                BuildingMembership.user_id == user.id,
            )
        )
        if membership is None:
            db.session.add(
                BuildingMembership(
                    building_id=term.building_id,
                    user_id=user.id,
                    role="building_admin",
                )
            )
        term.user = user
    return created


def source_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_legacy(path):
    resolved = Path(path).resolve().as_posix()
    connection = sqlite3.connect(f"file:{resolved}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def import_legacy(building: Building, path):
    other_building = db.session.scalar(
        db.select(Building).where(Building.id != building.id).limit(1)
    )
    if other_building is not None:
        raise ValueError("Η αρχική βάση μπορεί να εισαχθεί μόνο στο πρώτο setup.")
    digest = source_hash(path)
    if db.session.scalar(db.select(Building).where(Building.legacy_source_hash == digest)):
        raise ValueError("Η συγκεκριμένη αρχική βάση έχει ήδη εισαχθεί.")

    connection = open_legacy(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Αποτυχία integrity check: {integrity}")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not EXPECTED_TABLES.issubset(tables):
            raise ValueError("Η αρχική βάση δεν έχει το αναμενόμενο schema.")

        category_map = {}
        for row in connection.execute("SELECT * FROM koi_category ORDER BY id"):
            item = ExpenseCategory(building=building, name=row["category"], legacy_id=row["id"])
            db.session.add(item)
            category_map[row["id"]] = item

        apartment_map = {}
        for row in connection.execute("SELECT * FROM koi_diamerisma ORDER BY id"):
            item = Apartment(
                building=building,
                name=row["name"],
                number=row["num"],
                floor=row["orofos"],
                square_meters=Decimal(str(row["sizesm"])),
                owner=row["owner"],
                occupant=row["guest"],
                legacy_id=row["id"],
            )
            db.session.add(item)
            apartment_map[row["id"]] = item
        db.session.flush()

        manager_rows = list(
            connection.execute("SELECT * FROM koi_diaxeiristis ORDER BY date_from, id")
        )
        manager_map = {}
        for index, row in enumerate(manager_rows):
            next_date = (
                date.fromisoformat(manager_rows[index + 1]["date_from"])
                if index + 1 < len(manager_rows)
                else None
            )
            item = ManagerTerm(
                building_id=building.id,
                display_name=row["name"],
                date_from=date.fromisoformat(row["date_from"]),
                date_to=next_date - timedelta(days=1) if next_date else None,
                legacy_id=row["id"],
            )
            db.session.add(item)
            manager_map[row["id"]] = item
        db.session.flush()
        materialize_legacy_managers(building.id)

        matrix = {
            (row["diamerisma_id"], row["category_id"]): row["xiliosta"]
            for row in connection.execute("SELECT * FROM koi_xiliosta")
        }
        for old_apartment_id, apartment in apartment_map.items():
            for old_category_id, category in category_map.items():
                db.session.add(
                    Millesimal(
                        apartment=apartment,
                        category=category,
                        value=matrix.get((old_apartment_id, old_category_id), 0),
                    )
                )
        for old_category_id in category_map:
            total = sum(
                matrix.get((old_apartment_id, old_category_id), 0)
                for old_apartment_id in apartment_map
            )
            if total != 1000:
                raise ValueError(
                    f"Τα ιστορικά χιλιοστά της κατηγορίας {old_category_id} είναι {total}."
                )

        period_map = {}
        for row in connection.execute("SELECT * FROM koi_koinoxrista ORDER BY ekdosi, id"):
            manager = manager_map[row["diaxeiristis_id"]]
            item = Period(
                id=row["id"],
                building=building,
                issue_date=date.fromisoformat(row["ekdosi"]),
                comments=row["sxolia"],
                status="draft",
                manager_term=manager,
                manager_name_snapshot=manager.display_name,
            )
            db.session.add(item)
            period_map[row["id"]] = item
        db.session.flush()

        for row in connection.execute("SELECT * FROM koi_dapanes ORDER BY id"):
            db.session.add(
                Expense(
                    period=period_map[row["koinoxrista_id"]],
                    category=category_map[row["category_id"]],
                    invoice_date=date.fromisoformat(row["par_date"]),
                    invoice_number=row["par_num"],
                    description=row["par_per"],
                    amount=Decimal(str(row["value"])),
                    legacy_id=row["id"],
                )
            )
        db.session.flush()
        for period in period_map.values():
            build_allocations(period, reconstructed=True)

        building.legacy_source_hash = digest
        building.legacy_imported_at = utcnow()
        return {
            "categories": len(category_map),
            "apartments": len(apartment_map),
            "periods": len(period_map),
            "expenses": connection.execute("SELECT COUNT(*) FROM koi_dapanes").fetchone()[0],
            "hash": digest,
        }
    finally:
        connection.close()

import calendar
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from .database_maintenance import (
    DatabaseMaintenanceError,
    application_database_path,
    create_backup,
    delete_local_backup,
    list_backups,
    local_backup_path,
    restore_database,
    save_restore_upload,
)
from .extensions import db
from .legacy import import_legacy
from .models import (
    Apartment,
    Building,
    BuildingMembership,
    CashEntry,
    Expense,
    ExpenseCategory,
    ExpensePayment,
    Invitation,
    ManagerTerm,
    Millesimal,
    PasswordResetToken,
    Payee,
    Period,
    User,
    utcnow,
)
from .pdf_reports import (
    create_cash_receipt,
    create_payment_notices,
    create_payment_receipt,
    create_period_report,
    create_receipts_report,
)
from .permissions import membership_for, require_role, system_admin_required
from .security import issue_token, normalize_email
from .services import (
    BuildingWizardError,
    allocation_report,
    apartment_ledger,
    apartment_position,
    audit,
    build_allocations,
    cash_book,
    cash_report,
    compute_allocations,
    create_building_graph,
    delete_building,
    expense_in_ledger,
    expense_report,
    ledger_summary,
    manager_on,
    next_cash_entry_number,
    next_payment_number,
    payee_balance,
    payee_ledger,
    period_in_ledger,
    recurring_suggestions,
    replace_millesimals,
    require_cash,
)

main = Blueprint("main", __name__)


def parse_date(value, label="ημερομηνία"):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Μη έγκυρη {label}.") from exc


def form_error(exc, duplicate):
    """User-facing text for a failed save: never database or parser internals."""
    if isinstance(exc, IntegrityError):
        return duplicate
    if isinstance(exc, InvalidOperation) or (
        isinstance(exc, ValueError)
        and str(exc).startswith(("invalid literal", "could not convert"))
    ):
        return "Κάποιο αριθμητικό πεδίο δεν είναι έγκυρο."
    return str(exc)


APARTMENT_EXISTS = "Υπάρχει ήδη διαμέρισμα με αυτόν τον αριθμό."
EXPENSE_EXISTS = "Υπάρχει ήδη δαπάνη με τον ίδιο αριθμό και ημερομηνία παραστατικού."
PERIOD_EXISTS = "Υπάρχει ήδη περίοδος με αυτή την ημερομηνία έκδοσης."
CATEGORY_EXISTS = "Υπάρχει ήδη κατηγορία με αυτή την ονομασία."


def get_building(building_id, role="viewer"):
    building = db.get_or_404(Building, building_id)
    require_role(building.id, role)
    return building


@main.get("/")
@login_required
def dashboard():
    if current_user.is_system_admin:
        buildings = db.session.scalars(db.select(Building).order_by(Building.name)).all()
    else:
        buildings = db.session.scalars(
            db.select(Building)
            .join(BuildingMembership)
            .where(BuildingMembership.user_id == current_user.id)
            .order_by(Building.name)
        ).all()
    return render_template("dashboard.html", buildings=buildings)


@main.route("/setup", methods=["GET", "POST"])
@login_required
@system_admin_required
def initial_setup():
    imported = db.session.scalar(
        db.select(Building).where(Building.legacy_source_hash.is_not(None))
    )
    if imported:
        flash("Η αρχική βάση έχει ήδη εισαχθεί.", "info")
        return redirect(url_for("main.building_detail", building_id=imported.id))
    if db.session.scalar(db.select(db.func.count(Building.id))):
        flash(
            "Η αρχική βάση μπορεί να εισαχθεί μόνο πριν δημιουργηθεί το πρώτο κτίριο.",
            "error",
        )
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        source = Path(current_app.config["LEGACY_DATABASE"])
        if not source.is_file():
            flash("Δεν βρέθηκε η αρχική koinoxrista.db.", "error")
            return render_template("setup.html")
        building = Building(
            name=request.form.get("name", "").strip() or "Αρχικό κτίριο",
            address=request.form.get("address", "").strip(),
            postal_code=request.form.get("postal_code", "").strip(),
            created_by_id=current_user.id,
        )
        if not building.address or not building.postal_code:
            flash("Η διεύθυνση και ο ΤΚ είναι υποχρεωτικά.", "error")
            return render_template("setup.html")
        try:
            db.session.add(building)
            db.session.flush()
            db.session.add(
                BuildingMembership(
                    building_id=building.id, user_id=current_user.id, role="building_admin"
                )
            )
            result = import_legacy(building, source)
            audit("legacy_import", current_user.id, building.id, str(result))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            flash(f"Η εισαγωγή ακυρώθηκε χωρίς αλλαγές: {exc}", "error")
            return render_template("setup.html")
        flash(
            f"Εισήχθησαν {result['periods']} περίοδοι και {result['expenses']} δαπάνες.",
            "success",
        )
        return redirect(url_for("main.building_detail", building_id=building.id))
    return render_template("setup.html")


@main.route("/buildings/new", methods=["GET", "POST"])
@login_required
@system_admin_required
def building_create():
    if request.method == "POST":
        raw_payload = request.form.get("wizard_payload", "")
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        try:
            building = create_building_graph(payload, current_user.id)
            db.session.commit()
            flash("Το κτίριο δημιουργήθηκε με όλα τα στοιχεία του.", "success")
            return redirect(url_for("main.building_detail", building_id=building.id))
        except BuildingWizardError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return render_template(
                "building_form.html", initial_payload=payload, initial_step=exc.step
            )
        except IntegrityError:
            db.session.rollback()
            flash("Δεν δημιουργήθηκε το κτίριο. Ελέγξτε για διπλότυπα στοιχεία.", "error")
            return render_template("building_form.html", initial_payload=payload, initial_step=2)
    return render_template("building_form.html", initial_payload=None, initial_step=1)


def manager_candidates(building_id):
    """Active users who may manage the building: every administrator and the building's users."""
    members = db.select(BuildingMembership.user_id).where(
        BuildingMembership.building_id == building_id
    )
    return db.session.scalars(
        db.select(User)
        .where(
            User.is_active_account.is_(True),
            or_(User.is_system_admin.is_(True), User.id.in_(members)),
        )
        .order_by(User.display_name, User.email)
    ).all()


@main.get("/buildings/<int:building_id>")
@login_required
def building_detail(building_id):
    building = get_building(building_id)
    role = membership_for(building.id)
    periods_query = db.select(Period).where(Period.building_id == building.id)
    if role == "viewer":
        periods_query = periods_query.where(Period.status == "finalized")
    periods_pagination = db.paginate(
        periods_query.order_by(Period.issue_date.desc(), Period.id.desc()),
        page=request.args.get("page", 1, type=int),
        per_page=15,
        error_out=False,
    )
    session[f"building_page_{building.id}"] = periods_pagination.page
    managers = db.session.scalars(
        db.select(ManagerTerm)
        .where(ManagerTerm.building_id == building.id)
        .order_by(ManagerTerm.date_from.desc())
    ).all()
    eligible_users = manager_candidates(building.id)
    cash = None
    if role == "admin" and request.args.get("tab") == "cash":
        cash = ledger_summary(building)
        cash["report"] = cash_report(building)
        # ponytail: whole book in memory, newest 50 shown; paginate if a building outgrows it
        cash["book"] = list(reversed(cash_book(building)))[:50]
        cash["new_payment"] = db.session.scalar(
            db.select(ExpensePayment).where(
                ExpensePayment.id == request.args.get("new_payment", 0, type=int),
                ExpensePayment.building_id == building.id,
            )
        )
    payees = building_payees(building.id)
    return render_template(
        "building_detail.html",
        cash=cash,
        payees=payees,
        payee_balances={payee.id: payee_balance(payee) for payee in payees}
        if role == "admin" and request.args.get("tab") == "payees"
        else {},
        building=building,
        periods=periods_pagination.items,
        periods_pagination=periods_pagination,
        managers=managers,
        eligible_users=eligible_users,
        role=role,
    )


@main.post("/buildings/<int:building_id>/details")
@login_required
def building_update(building_id):
    building = get_building(building_id, "admin")
    name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip()
    postal_code = request.form.get("postal_code", "").strip()
    display_mode = request.form.get("apartment_display_mode", "occupant")
    if not all((name, address, postal_code)):
        flash("Η ονομασία, η διεύθυνση και ο ΤΚ είναι υποχρεωτικά.", "error")
    elif display_mode not in {"name", "owner", "occupant"}:
        flash("Ο τρόπος εμφάνισης διαμερισμάτων δεν είναι έγκυρος.", "error")
    else:
        building.name = name
        building.address = address
        building.postal_code = postal_code
        building.apartment_display_mode = display_mode
        audit("building_updated", current_user.id, building.id)
        db.session.commit()
        flash("Τα στοιχεία του κτιρίου ενημερώθηκαν.", "success")
    return redirect(url_for("main.building_detail", building_id=building.id))


@main.post("/buildings/<int:building_id>/delete")
@login_required
@system_admin_required
def building_delete(building_id):
    building = db.get_or_404(Building, building_id)
    back = redirect(url_for("main.building_detail", building_id=building.id))
    if request.form.get("confirmation", "").strip() != building.name:
        flash("Πληκτρολογήστε ακριβώς την ονομασία του κτιρίου για επιβεβαίωση.", "error")
        return back
    if not current_user.check_password(request.form.get("password", "")):
        flash("Ο κωδικός σας δεν είναι σωστός.", "error")
        return back
    try:
        backup_path = create_backup(prefix="before-building-delete")
    except DatabaseMaintenanceError as exc:
        flash(f"Η διαγραφή ακυρώθηκε γιατί δεν δημιουργήθηκε backup: {exc}", "error")
        return back
    name = building.name
    delete_building(building)
    audit("building_deleted", current_user.id, details=f"{name} · backup: {backup_path.name}")
    db.session.commit()
    flash(
        f"Το κτίριο «{name}» διαγράφηκε. Backup πριν τη διαγραφή: {backup_path.name}",
        "success",
    )
    return redirect(url_for("main.dashboard"))


@main.post("/buildings/<int:building_id>/apartments")
@login_required
def apartment_add(building_id):
    building = get_building(building_id, "admin")
    try:
        apartment = Apartment(
            building=building,
            name=request.form.get("name", "").strip(),
            number=int(request.form.get("number", "")),
            floor=int(request.form.get("floor", "0")),
            square_meters=Decimal(request.form.get("square_meters", "0")),
            owner=request.form.get("owner", "").strip(),
            occupant=request.form.get("occupant", "").strip(),
        )
        if not apartment.name:
            raise ValueError("Η ονομασία είναι υποχρεωτική.")
        db.session.add(apartment)
        db.session.flush()
        for category in building.categories:
            db.session.add(Millesimal(apartment=apartment, category=category, value=0))
        audit("apartment_added", current_user.id, building.id, apartment.name)
        db.session.commit()
        flash("Το διαμέρισμα προστέθηκε. Ενημερώστε άμεσα τη μήτρα χιλιοστών.", "success")
    except (ValueError, InvalidOperation, IntegrityError) as exc:
        db.session.rollback()
        flash(f"Δεν προστέθηκε το διαμέρισμα: {form_error(exc, APARTMENT_EXISTS)}", "error")
    return redirect(url_for("main.building_detail", building_id=building.id, tab="apartments"))


@main.route("/apartments/<int:apartment_id>/edit", methods=["GET", "POST"])
@login_required
def apartment_edit(apartment_id):
    apartment = db.get_or_404(Apartment, apartment_id)
    require_role(apartment.building_id, "admin")
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            if not name:
                raise ValueError("Η ονομασία είναι υποχρεωτική.")
            apartment.name = name
            apartment.number = int(request.form.get("number", ""))
            apartment.floor = int(request.form.get("floor", "0"))
            apartment.square_meters = Decimal(request.form.get("square_meters", "0"))
            apartment.owner = request.form.get("owner", "").strip()
            apartment.occupant = request.form.get("occupant", "").strip()
            audit(
                "apartment_updated",
                current_user.id,
                apartment.building_id,
                apartment.display_label(),
            )
            db.session.commit()
            flash("Τα στοιχεία του διαμερίσματος ενημερώθηκαν.", "success")
            return redirect(
                url_for("main.building_detail", building_id=apartment.building_id, tab="apartments")
            )
        except (ValueError, InvalidOperation, IntegrityError) as exc:
            db.session.rollback()
            flash(f"Δεν αποθηκεύτηκαν οι αλλαγές: {form_error(exc, APARTMENT_EXISTS)}", "error")
    return render_template("apartment_form.html", apartment=apartment)


@main.route("/buildings/<int:building_id>/categories/new", methods=["GET", "POST"])
@login_required
def category_add(building_id):
    building = get_building(building_id, "admin")
    apartments = sorted(building.apartments, key=lambda item: (item.number, item.id))
    if not apartments:
        flash("Προσθέστε πρώτα τα διαμερίσματα του κτιρίου.", "error")
        return redirect(url_for("main.building_detail", building_id=building.id, tab="apartments"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            if not name:
                raise ValueError("Η ονομασία κατηγορίας είναι υποχρεωτική.")
            values = [int(request.form.get(f"a_{item.id}", "0")) for item in apartments]
            if any(value < 0 or value > 1000 for value in values) or sum(values) != 1000:
                raise ValueError(f"Τα χιλιοστά πρέπει να αθροίζουν σε 1000, όχι {sum(values)}.")
            category = ExpenseCategory(building=building, name=name)
            db.session.add(category)
            db.session.flush()
            for apartment, value in zip(apartments, values, strict=True):
                db.session.add(Millesimal(apartment=apartment, category=category, value=value))
            audit("category_added", current_user.id, building.id, name)
            db.session.commit()
            flash("Η κατηγορία προστέθηκε.", "success")
            return redirect(
                url_for("main.building_detail", building_id=building.id, tab="categories")
            )
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(f"Δεν προστέθηκε η κατηγορία: {form_error(exc, CATEGORY_EXISTS)}", "error")
    return render_template("category_form.html", building=building, apartments=apartments)


@main.route("/buildings/<int:building_id>/millesimals", methods=["GET", "POST"])
@login_required
def millesimals(building_id):
    building = get_building(building_id, "admin")
    apartments = sorted(building.apartments, key=lambda item: (item.number, item.id))
    categories = sorted(building.categories, key=lambda item: item.id)
    existing = {
        (item.apartment_id, item.category_id): item.value
        for item in db.session.scalars(
            db.select(Millesimal).where(
                Millesimal.apartment_id.in_([item.id for item in apartments] or [-1]),
                Millesimal.category_id.in_([item.id for item in categories] or [-1]),
            )
        )
    }
    if request.method == "POST":
        values = {
            (apartment.id, category.id): request.form.get(f"m_{apartment.id}_{category.id}", "0")
            for apartment in apartments
            for category in categories
        }
        try:
            replace_millesimals(apartments, categories, values)
            audit("millesimals_replaced", current_user.id, building.id)
            db.session.commit()
            flash("Η μήτρα αποθηκεύτηκε και όλες οι κατηγορίες αθροίζουν σε 1000.", "success")
            return redirect(
                url_for("main.building_detail", building_id=building.id, tab="categories")
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            existing = {
                key: int(value) if str(value).isdigit() else value for key, value in values.items()
            }
    return render_template(
        "millesimals.html",
        building=building,
        apartments=apartments,
        categories=categories,
        values=existing,
    )


@main.post("/buildings/<int:building_id>/managers")
@login_required
def manager_assign(building_id):
    building = get_building(building_id, "admin")
    user_id = request.form.get("user_id", type=int)
    user = next((item for item in manager_candidates(building.id) if item.id == user_id), None)
    if not user:
        abort(400)
    start = parse_date(request.form.get("date_from"))
    active = db.session.scalar(
        db.select(ManagerTerm).where(
            ManagerTerm.building_id == building.id, ManagerTerm.date_to.is_(None)
        )
    )
    if active:
        if start <= active.date_from:
            flash("Η νέα θητεία πρέπει να αρχίζει μετά την τρέχουσα.", "error")
            return redirect(
                url_for("main.building_detail", building_id=building.id, tab="managers")
            )
        active.date_to = start - timedelta(days=1)
    db.session.add(
        ManagerTerm(
            building_id=building.id,
            user_id=user.id,
            display_name=user.display_name,
            date_from=start,
        )
    )
    audit("manager_assigned", current_user.id, building.id, user.display_name)
    db.session.commit()
    return redirect(url_for("main.building_detail", building_id=building.id, tab="managers"))


def end_of_next_month(day):
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, calendar.monthrange(year, month)[1])


def suggested_issue_date(building_id):
    """Last day of the month after the building's latest period (or of this month, if none)."""
    latest = db.session.scalar(
        db.select(db.func.max(Period.issue_date)).where(Period.building_id == building_id)
    )
    if latest is None:
        today = date.today()
        return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    return end_of_next_month(latest)


@main.route("/buildings/<int:building_id>/periods/new", methods=["GET", "POST"])
@login_required
def period_create(building_id):
    building = get_building(building_id, "admin")
    if request.method == "POST":
        try:
            issue_date = parse_date(request.form.get("issue_date"), "ημερομηνία έκδοσης")
            manager = manager_on(building.id, issue_date)
            if not manager:
                raise ValueError("Δεν υπάρχει ενεργός διαχειριστής για αυτή την ημερομηνία.")
            period = Period(
                building=building,
                issue_date=issue_date,
                comments=request.form.get("comments", "").strip(),
                manager_term=manager,
                manager_name_snapshot=manager.display_name,
            )
            db.session.add(period)
            db.session.commit()
            return redirect(url_for("main.period_detail", period_id=period.id))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(f"Δεν δημιουργήθηκε η περίοδος: {form_error(exc, PERIOD_EXISTS)}", "error")
    return render_template(
        "period_form.html", building=building, suggested_date=suggested_issue_date(building.id)
    )


@main.route("/periods/<int:period_id>/edit", methods=["GET", "POST"])
@login_required
def period_edit(period_id):
    period = db.get_or_404(Period, period_id)
    require_role(period.building_id, "admin")
    if period.status != "draft":
        abort(409)
    if request.method == "POST":
        try:
            issue_date = parse_date(request.form.get("issue_date"), "ημερομηνία έκδοσης")
            manager = manager_on(period.building_id, issue_date)
            if not manager:
                raise ValueError("Δεν υπάρχει ενεργός διαχειριστής για αυτή την ημερομηνία.")
            period.issue_date = issue_date
            period.comments = request.form.get("comments", "").strip()
            period.manager_term = manager
            period.manager_name_snapshot = manager.display_name
            audit("period_updated", current_user.id, period.building_id, str(period.id))
            db.session.commit()
            flash("Τα στοιχεία της περιόδου ενημερώθηκαν.", "success")
            return redirect(url_for("main.period_detail", period_id=period.id))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(f"Δεν αποθηκεύτηκαν οι αλλαγές: {form_error(exc, PERIOD_EXISTS)}", "error")
    return render_template("period_edit_form.html", period=period)


@main.get("/periods/<int:period_id>")
@login_required
def period_detail(period_id):
    period = db.get_or_404(Period, period_id)
    role = require_role(period.building_id)
    if role == "viewer" and period.status != "finalized":
        abort(403)
    allocation_error = None
    if period.status == "finalized":
        report = allocation_report(period)
    else:
        try:
            report = allocation_report(period, compute_allocations(period))
        except ValueError as exc:
            report, allocation_error = None, str(exc)
    return render_template(
        "period_detail.html",
        period=period,
        role=role,
        expense_report=expense_report(period),
        payees=building_payees(period.building_id),
        cash_active=role == "admin" and period_in_ledger(period),
        recurring=recurring_suggestions(period)
        if role == "admin" and period.status == "draft"
        else (None, []),
        new_payment=db.session.scalar(
            db.select(ExpensePayment)
            .join(ExpensePayment.expense)
            .where(
                ExpensePayment.id == request.args.get("new_payment", 0, type=int),
                Expense.period_id == period.id,
            )
        ),
        allocation_report=report,
        allocation_error=allocation_error,
    )


def finalized_period_for_report(period_id):
    period = db.get_or_404(Period, period_id)
    require_role(period.building_id)
    if period.status != "finalized":
        abort(409, description="Η περίοδος πρέπει να οριστικοποιηθεί πριν την έκδοση PDF.")
    return period


@main.get("/periods/<int:period_id>/report.pdf")
@login_required
def period_report_pdf(period_id):
    period = finalized_period_for_report(period_id)
    return send_file(
        BytesIO(create_period_report(period)),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"koinoxrista-{period.issue_date.isoformat()}.pdf",
    )


@main.get("/periods/<int:period_id>/receipts.pdf")
@login_required
def period_receipts_pdf(period_id):
    period = finalized_period_for_report(period_id)
    return send_file(
        BytesIO(create_receipts_report(period)),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"apodeixeis-{period.issue_date.isoformat()}.pdf",
    )


@main.get("/periods/<int:period_id>/notices.pdf")
@login_required
def period_notices_pdf(period_id):
    period = finalized_period_for_report(period_id)
    return send_file(
        BytesIO(create_payment_notices(period)),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"eidopoiiseis-{period.issue_date.isoformat()}.pdf",
    )


def parse_amount(value, label):
    amount = Decimal(value.strip().replace(",", ".") or "0").quantize(Decimal("0.01"))
    if not amount.is_finite():
        raise ValueError(f"Μη έγκυρο ποσό: {label}.")
    return amount


@main.route("/buildings/<int:building_id>/cash/setup", methods=["GET", "POST"])
@login_required
def cash_setup(building_id):
    building = get_building(building_id, "admin")
    apartments = sorted(building.apartments, key=lambda item: (item.number, item.id))
    if request.method == "POST":
        try:
            building.ledger_start_date = parse_date(
                request.form.get("ledger_start_date"), "ημερομηνία έναρξης"
            )
            building.opening_cash = parse_amount(
                request.form.get("opening_cash", "0"), "αρχικό ταμείο"
            )
            if building.opening_cash < 0:
                raise ValueError("Τα αρχικά μετρητά δεν μπορεί να είναι αρνητικά.")
            for apartment in apartments:
                label = apartment.display_label()
                apartment.opening_balance = parse_amount(
                    request.form.get(f"balance_{apartment.id}", "0"), label
                )
            require_cash(building)
            audit(
                "cash_setup_updated",
                current_user.id,
                building.id,
                building.ledger_start_date.isoformat(),
            )
            db.session.commit()
            flash("Τα αρχικά υπόλοιπα του ταμείου αποθηκεύτηκαν.", "success")
            return redirect(url_for("main.building_detail", building_id=building.id, tab="cash"))
        except (ValueError, InvalidOperation) as exc:
            db.session.rollback()
            flash(f"Δεν αποθηκεύτηκαν τα υπόλοιπα: {form_error(exc, '')}", "error")
    return render_template("cash_setup.html", building=building, apartments=apartments)


@main.route("/buildings/<int:building_id>/cash/reserves", methods=["GET", "POST"])
@login_required
def cash_reserves(building_id):
    building = get_building(building_id, "admin")
    if building.ledger_start_date is None:
        abort(409, description="Ορίστε πρώτα την έναρξη του ταμείου.")
    apartments = sorted(building.apartments, key=lambda item: (item.number, item.id))
    if request.method == "POST":
        try:
            for apartment in apartments:
                label = apartment.display_label()
                reserve = parse_amount(request.form.get(f"reserve_{apartment.id}", "0"), label)
                if reserve < 0:
                    raise ValueError(f"Το αποθεματικό δεν μπορεί να είναι αρνητικό: {label}.")
                apartment.opening_reserve = reserve
                if apartment_position(apartment)[1] < 0:
                    raise ValueError(
                        f"Μετά τις επιστροφές το αποθεματικό θα γινόταν αρνητικό: {label}."
                    )
            audit("cash_reserves_updated", current_user.id, building.id)
            db.session.commit()
            flash("Το αρχικό αποθεματικό αποθηκεύτηκε.", "success")
            return redirect(url_for("main.building_detail", building_id=building.id, tab="cash"))
        except (ValueError, InvalidOperation) as exc:
            db.session.rollback()
            flash(f"Δεν αποθηκεύτηκε το αποθεματικό: {form_error(exc, '')}", "error")
    return render_template(
        "cash_reserves.html",
        building=building,
        rows=[(apartment, apartment_position(apartment)[1]) for apartment in apartments],
    )


@main.route("/buildings/<int:building_id>/cash/new", methods=["GET", "POST"])
@login_required
def cash_entry_new(building_id):
    building = get_building(building_id, "admin")
    if building.ledger_start_date is None:
        abort(409, description="Ορίστε πρώτα την έναρξη του ταμείου.")
    apartments = sorted(building.apartments, key=lambda item: (item.number, item.id))
    if request.method == "POST":
        try:
            apartment = next(
                (
                    item
                    for item in apartments
                    if str(item.id) == request.form.get("apartment_id", "")
                ),
                None,
            )
            if apartment is None:
                raise ValueError("Επιλέξτε διαμέρισμα.")
            kind = request.form.get("kind", "")
            if kind not in CashEntry.KINDS:
                raise ValueError("Επιλέξτε είδος κίνησης.")
            entry_date = parse_date(request.form.get("date"))
            if entry_date < building.ledger_start_date:
                raise ValueError("Η ημερομηνία είναι πριν την έναρξη του ταμείου.")
            amount = parse_amount(request.form.get("amount", ""), "ποσό")
            if amount <= 0:
                raise ValueError("Το ποσό πρέπει να είναι θετικό.")
            if kind == "reserve_out":
                if amount > apartment_position(apartment)[1]:
                    raise ValueError("Το ποσό ξεπερνά το αποθεματικό του διαμερίσματος.")
                require_cash(building, add=(entry_date, -amount))
            entry = CashEntry(
                building=building,
                apartment=apartment,
                number=next_cash_entry_number(building),
                kind=kind,
                date=entry_date,
                amount=amount,
                party_name=request.form.get("party_name", "").strip()[:100],
                notes=request.form.get("notes", "").strip()[:200],
                created_by_id=current_user.id,
            )
            db.session.add(entry)
            db.session.flush()
            audit(
                "cash_entry_added",
                current_user.id,
                building.id,
                f"{entry.number} · {kind} · {amount}",
            )
            db.session.commit()
            flash(f"Καταχωρήθηκε η απόδειξη αρ. {entry.number}.", "success")
            return redirect(
                url_for("main.apartment_ledger_view", apartment_id=apartment.id, new=entry.id)
            )
        except (ValueError, InvalidOperation, IntegrityError) as exc:
            db.session.rollback()
            message = form_error(exc, "Ο αριθμός απόδειξης δόθηκε ήδη. Δοκιμάστε ξανά.")
            flash(f"Δεν καταχωρήθηκε η κίνηση: {message}", "error")
    selected = request.form.get("apartment_id") or request.args.get("apartment_id", "")
    suggested = {str(item.id): f"{max(apartment_position(item)[0], 0):.2f}" for item in apartments}
    return render_template(
        "cash_entry_form.html",
        building=building,
        apartments=apartments,
        selected=selected,
        suggested=suggested,
        kinds=CashEntry.KINDS,
        today=date.today(),
    )


@main.get("/apartments/<int:apartment_id>/ledger")
@login_required
def apartment_ledger_view(apartment_id):
    apartment = db.get_or_404(Apartment, apartment_id)
    require_role(apartment.building_id, "admin")
    new_entry = db.session.get(CashEntry, request.args.get("new", 0, type=int))
    if new_entry is not None and new_entry.apartment_id != apartment.id:
        new_entry = None
    return render_template(
        "apartment_ledger.html",
        apartment=apartment,
        building=apartment.building,
        rows=apartment_ledger(apartment),
        new_entry=new_entry,
    )


def cash_entry_for_admin(entry_id):
    entry = db.get_or_404(CashEntry, entry_id)
    require_role(entry.building_id, "admin")
    return entry


@main.get("/cash-entries/<int:entry_id>/receipt.pdf")
@login_required
def cash_entry_receipt_pdf(entry_id):
    entry = cash_entry_for_admin(entry_id)
    return send_file(
        BytesIO(create_cash_receipt(entry)),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"apodeixi-{entry.number}.pdf",
    )


@main.post("/cash-entries/<int:entry_id>/void")
@login_required
def cash_entry_void(entry_id):
    entry = cash_entry_for_admin(entry_id)
    reason = request.form.get("reason", "").strip()[:200]
    linked = (
        entry.covered_payment
        if entry.covered_payment and not entry.covered_payment.voided_at
        else None
    )
    try:
        if entry.kind != "reserve_out" and linked is None:
            require_cash(entry.building, skip=entry)
        blocked = None
    except ValueError as exc:
        blocked = str(exc)
    if entry.voided_at:
        flash("Η απόδειξη είναι ήδη ακυρωμένη.", "error")
    elif not reason:
        flash("Γράψτε τον λόγο ακύρωσης.", "error")
    elif blocked:
        flash(f"Η απόδειξη δεν ακυρώνεται. {blocked}", "error")
    else:
        entry.voided_at = utcnow()
        entry.void_reason = reason
        if linked:
            linked.voided_at, linked.void_reason = entry.voided_at, reason
            flash(f"Ακυρώθηκε μαζί και η πληρωμή δαπάνης αρ. {linked.number}.", "success")
        audit("cash_entry_voided", current_user.id, entry.building_id, f"{entry.number} · {reason}")
        db.session.commit()
        flash(f"Η απόδειξη αρ. {entry.number} ακυρώθηκε.", "success")
    return redirect(url_for("main.apartment_ledger_view", apartment_id=entry.apartment_id))


def record_payment(building, expense, payment_date, amount, notes, paid_by=None):
    """Validate and add a payment of the expense (caller commits).

    paid_by: an apartment that paid the payee out of pocket. That is booked as the apartment
    paying the fund (a receipt crediting its card) and the fund paying the payee, same day:
    cash is unchanged, the expense is settled and the apartment gets the credit.
    """
    if payment_date < building.ledger_start_date:
        raise ValueError("Η ημερομηνία πληρωμής είναι πριν την έναρξη του ταμείου.")
    if amount <= 0:
        raise ValueError("Το ποσό πληρωμής πρέπει να είναι θετικό.")
    outstanding = expense.amount - expense.paid
    if amount > outstanding:
        raise ValueError(f"Το ποσό ξεπερνά το υπόλοιπο της δαπάνης ({outstanding} €).")
    entry = None
    if paid_by is not None:
        entry = CashEntry(
            building=building,
            apartment=paid_by,
            number=next_cash_entry_number(building),
            kind="collection",
            date=payment_date,
            amount=amount,
            party_name=paid_by.occupant or paid_by.owner,
            notes=f"Πλήρωσε «{expense.description}» στον/στην {expense.payee.name}"[:200],
            created_by_id=current_user.id,
        )
        db.session.add(entry)
        db.session.flush()
        notes = notes or f"Πληρώθηκε από {paid_by.display_label()} (είσπραξη αρ. {entry.number})"
    require_cash(building, add=(payment_date, -amount))
    payment = ExpensePayment(
        building=building,
        expense=expense,
        cash_entry=entry,
        number=next_payment_number(building),
        date=payment_date,
        amount=amount,
        payee=expense.payee.name,
        notes=notes,
        created_by_id=current_user.id,
    )
    db.session.add(payment)
    db.session.flush()
    audit(
        "expense_paid",
        current_user.id,
        building.id,
        f"{payment.number} · δαπάνη {expense.id} · {amount}",
    )
    return payment


@main.route("/expenses/<int:expense_id>/pay", methods=["GET", "POST"])
@login_required
def expense_pay(expense_id):
    expense = db.get_or_404(Expense, expense_id)
    building = get_building(expense.period.building_id, "admin")
    if not expense_in_ledger(expense):
        abort(409, description="Η δαπάνη δεν ανήκει σε περίοδο του ταμείου.")
    outstanding = expense.amount - expense.paid
    if request.method == "POST":
        try:
            if expense.payee is None:
                expense.payee = payee_from_form(building.id)
            payment = record_payment(
                building,
                expense,
                parse_date(request.form.get("date")),
                parse_amount(request.form.get("amount", ""), "ποσό"),
                request.form.get("notes", "").strip()[:200],
                paid_by=apartment_from_form(building),
            )
            db.session.commit()
            flash(f"Καταχωρήθηκε η πληρωμή αρ. {payment.number}.", "success")
            return redirect(
                url_for(
                    "main.building_detail",
                    building_id=building.id,
                    tab="cash",
                    new_payment=payment.id,
                )
            )
        except (ValueError, InvalidOperation, IntegrityError) as exc:
            db.session.rollback()
            message = form_error(exc, "Ο αριθμός πληρωμής δόθηκε ήδη. Δοκιμάστε ξανά.")
            flash(f"Δεν καταχωρήθηκε η πληρωμή: {message}", "error")
    return render_template(
        "expense_payment_form.html",
        apartments=sorted(building.apartments, key=lambda item: (item.number, item.id)),
        expense=expense,
        payees=building_payees(building.id),
        building=building,
        outstanding=outstanding,
        today=date.today(),
    )


def payment_for_admin(payment_id):
    payment = db.get_or_404(ExpensePayment, payment_id)
    require_role(payment.building_id, "admin")
    return payment


@main.get("/expense-payments/<int:payment_id>/receipt.pdf")
@login_required
def expense_payment_receipt_pdf(payment_id):
    payment = payment_for_admin(payment_id)
    return send_file(
        BytesIO(create_payment_receipt(payment)),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"pliromi-{payment.number}.pdf",
    )


@main.post("/expense-payments/<int:payment_id>/void")
@login_required
def expense_payment_void(payment_id):
    payment = payment_for_admin(payment_id)
    reason = request.form.get("reason", "").strip()[:200]
    if payment.voided_at:
        flash("Η πληρωμή είναι ήδη ακυρωμένη.", "error")
    elif not reason:
        flash("Γράψτε τον λόγο ακύρωσης.", "error")
    else:
        payment.voided_at = utcnow()
        payment.void_reason = reason
        if payment.cash_entry and not payment.cash_entry.voided_at:
            payment.cash_entry.voided_at, payment.cash_entry.void_reason = payment.voided_at, reason
            flash(
                f"Ακυρώθηκε μαζί και η απόδειξη είσπραξης αρ. {payment.cash_entry.number}.",
                "success",
            )
        audit(
            "expense_payment_voided",
            current_user.id,
            payment.building_id,
            f"{payment.number} · {reason}",
        )
        db.session.commit()
        flash(f"Η πληρωμή αρ. {payment.number} ακυρώθηκε.", "success")
    return redirect(url_for("main.building_detail", building_id=payment.building_id, tab="cash"))


def apartment_from_form(building):
    """The apartment chosen as "paid by", or None when the fund paid."""
    value = request.form.get("paid_by", "")
    if not value:
        return None
    apartment = next((item for item in building.apartments if str(item.id) == value), None)
    if apartment is None:
        raise ValueError("Μη έγκυρο διαμέρισμα.")
    return apartment


def building_payees(building_id):
    return db.session.scalars(
        db.select(Payee).where(Payee.building_id == building_id).order_by(Payee.name)
    ).all()


def payee_from_form(building_id):
    """The payee by id, or by typed name (an unknown name creates a new payee)."""
    payee_id = request.form.get("payee_id", 0, type=int)
    if payee_id:
        payee = db.session.scalar(
            db.select(Payee).where(Payee.id == payee_id, Payee.building_id == building_id)
        )
        if payee is None:
            raise ValueError("Επιλέξτε δικαιούχο.")
        return payee
    name = " ".join(request.form.get("payee_name", "").split())[:100]
    if not name:
        raise ValueError("Επιλέξτε ή γράψτε δικαιούχο.")
    # casefold in Python: SQLite's lower() ignores Greek
    for payee in building_payees(building_id):
        if payee.name.casefold() == name.casefold():
            return payee
    payee = Payee(building_id=building_id, name=name)
    db.session.add(payee)
    audit("payee_added", current_user.id, building_id, name)
    return payee


PAYEE_EXISTS = "Υπάρχει ήδη δικαιούχος με αυτό το όνομα."


@main.post("/buildings/<int:building_id>/payees")
@login_required
def payee_add(building_id):
    building = get_building(building_id, "admin")
    name = request.form.get("name", "").strip()[:100]
    try:
        if not name:
            raise ValueError("Το όνομα είναι υποχρεωτικό.")
        db.session.add(Payee(building=building, name=name))
        audit("payee_added", current_user.id, building.id, name)
        db.session.commit()
        flash(f"Προστέθηκε ο δικαιούχος «{name}».", "success")
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(f"Δεν προστέθηκε ο δικαιούχος: {form_error(exc, PAYEE_EXISTS)}", "error")
    return redirect(url_for("main.building_detail", building_id=building.id, tab="payees"))


@main.get("/payees/<int:payee_id>/ledger")
@login_required
def payee_ledger_view(payee_id):
    payee = db.get_or_404(Payee, payee_id)
    require_role(payee.building_id, "admin")
    return render_template(
        "payee_ledger.html",
        payee=payee,
        building=payee.building,
        rows=payee_ledger(payee),
    )


@main.post("/payees/<int:payee_id>/rename")
@login_required
def payee_rename(payee_id):
    payee = db.get_or_404(Payee, payee_id)
    require_role(payee.building_id, "admin")
    name = request.form.get("name", "").strip()[:100]
    try:
        if not name:
            raise ValueError("Το όνομα είναι υποχρεωτικό.")
        audit("payee_renamed", current_user.id, payee.building_id, f"{payee.name} → {name}")
        payee.name = name
        db.session.commit()
        flash("Ο δικαιούχος μετονομάστηκε.", "success")
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(f"Δεν αποθηκεύτηκε: {form_error(exc, PAYEE_EXISTS)}", "error")
    return redirect(url_for("main.building_detail", building_id=payee.building_id, tab="payees"))


@main.post("/periods/<int:period_id>/recurring")
@login_required
def expense_add_recurring(period_id):
    period = db.get_or_404(Period, period_id)
    require_role(period.building_id, "admin")
    if period.status != "draft":
        abort(409)
    _source, suggestions = recurring_suggestions(period)
    chosen = set(request.form.getlist("copy"))
    added = 0
    try:
        for item in suggestions:
            key = str(item.source.id)
            if key not in chosen:
                continue
            label = item.source.description
            invoice_number = request.form.get(f"number_{key}", "").strip()
            description = request.form.get(f"description_{key}", "").strip()
            if not invoice_number or not description:
                raise ValueError(f"Ο αριθμός και η περιγραφή είναι υποχρεωτικά: {label}.")
            db.session.add(
                Expense(
                    period=period,
                    category=item.source.category,
                    payee=item.source.payee,
                    recurring=True,
                    invoice_date=parse_date(request.form.get(f"date_{key}"), "ημερομηνία"),
                    invoice_number=invoice_number,
                    description=description,
                    amount=parse_amount(request.form.get(f"amount_{key}", ""), label),
                )
            )
            added += 1
        db.session.flush()
        audit("recurring_expenses_added", current_user.id, period.building_id, str(added))
        db.session.commit()
        flash(f"Προστέθηκαν {added} επαναλαμβανόμενες δαπάνες.", "success")
    except (ValueError, InvalidOperation, IntegrityError) as exc:
        db.session.rollback()
        flash(f"Δεν προστέθηκαν οι δαπάνες: {form_error(exc, EXPENSE_EXISTS)}", "error")
    return redirect(url_for("main.period_detail", period_id=period.id))


@main.post("/periods/<int:period_id>/expenses")
@login_required
def expense_add(period_id):
    period = db.get_or_404(Period, period_id)
    require_role(period.building_id, "admin")
    if period.status != "draft":
        abort(409)
    payment = None
    try:
        expense = Expense(period=period)
        update_expense_from_form(expense, period)
        db.session.add(expense)
        if request.form.get("paid_cash"):
            if not period_in_ledger(period):
                raise ValueError("Ορίστε πρώτα την έναρξη του ταμείου.")
            db.session.flush()
            payment = record_payment(
                period.building,
                expense,
                expense.invoice_date,
                expense.amount,
                "Πληρωμή μετρητοίς",
            )
        db.session.commit()
        if payment:
            flash(f"Η δαπάνη προστέθηκε και πληρώθηκε (πληρωμή αρ. {payment.number}).", "success")
        else:
            flash("Η δαπάνη προστέθηκε.", "success")
    except (ValueError, InvalidOperation, IntegrityError) as exc:
        db.session.rollback()
        flash(f"Δεν προστέθηκε η δαπάνη: {form_error(exc, EXPENSE_EXISTS)}", "error")
    return redirect(
        url_for(
            "main.period_detail",
            period_id=period.id,
            new_payment=payment.id if payment else None,
        )
    )


def update_expense_from_form(expense, period):
    category_id = int(request.form.get("category_id", 0))
    category = db.session.scalar(
        db.select(ExpenseCategory).where(
            ExpenseCategory.id == category_id,
            ExpenseCategory.building_id == period.building_id,
        )
    )
    if not category:
        raise ValueError("Μη έγκυρη κατηγορία.")

    expense.payee = payee_from_form(period.building_id)
    expense.recurring = bool(request.form.get("recurring"))
    invoice_number = request.form.get("invoice_number", "").strip()
    description = request.form.get("description", "").strip()
    if not invoice_number or not description:
        raise ValueError("Ο αριθμός και η περιγραφή είναι υποχρεωτικά.")

    expense.category = category
    expense.invoice_date = parse_date(request.form.get("invoice_date"))
    expense.invoice_number = invoice_number
    expense.description = description
    amount = Decimal(request.form.get("amount", ""))
    if amount < expense.paid:
        raise ValueError(
            f"Το ποσό δεν μπορεί να είναι μικρότερο από τα πληρωμένα ({expense.paid} €)."
        )
    expense.amount = amount


@main.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def expense_edit(expense_id):
    expense = db.get_or_404(Expense, expense_id)
    period = expense.period
    require_role(period.building_id, "admin")
    if period.status != "draft":
        abort(409)
    if request.method == "POST":
        try:
            update_expense_from_form(expense, period)
            audit("expense_updated", current_user.id, period.building_id, str(expense.id))
            db.session.commit()
            flash("Η δαπάνη ενημερώθηκε.", "success")
            return redirect(url_for("main.period_detail", period_id=period.id))
        except (ValueError, InvalidOperation, IntegrityError) as exc:
            db.session.rollback()
            flash(f"Δεν αποθηκεύτηκαν οι αλλαγές: {form_error(exc, EXPENSE_EXISTS)}", "error")
    return render_template(
        "expense_form.html",
        expense=expense,
        period=period,
        payees=building_payees(period.building_id),
    )


@main.post("/expenses/<int:expense_id>/delete")
@login_required
def expense_delete(expense_id):
    expense = db.get_or_404(Expense, expense_id)
    period = expense.period
    require_role(period.building_id, "admin")
    if period.status != "draft":
        abort(409)
    if expense.payments:
        flash("Η δαπάνη έχει καταχωρημένες πληρωμές και δεν μπορεί να διαγραφεί.", "error")
        return redirect(url_for("main.period_detail", period_id=period.id))
    db.session.delete(expense)
    audit("expense_deleted", current_user.id, period.building_id, str(expense.id))
    db.session.commit()
    flash("Η δαπάνη διαγράφηκε.", "success")
    return redirect(url_for("main.period_detail", period_id=period.id))


@main.post("/periods/<int:period_id>/finalize")
@login_required
def period_finalize(period_id):
    period = db.get_or_404(Period, period_id)
    require_role(period.building_id, "admin")
    if period.status != "draft":
        abort(409)
    try:
        build_allocations(period)
        audit("period_finalized", current_user.id, period.building_id, str(period.id))
        db.session.commit()
        flash("Η περίοδος οριστικοποιήθηκε και η κατανομή αποθηκεύτηκε.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(f"Η οριστικοποίηση απέτυχε: {exc}", "error")
    return redirect(url_for("main.period_detail", period_id=period.id))


@main.post("/periods/<int:period_id>/reopen")
@login_required
def period_reopen(period_id):
    period = db.get_or_404(Period, period_id)
    require_role(period.building_id, "admin")
    if period.status != "finalized":
        abort(409)
    period.allocations.clear()
    period.status = "draft"
    period.finalized_at = None
    period.reopened_count += 1
    audit("period_reopened", current_user.id, period.building_id, str(period.id))
    db.session.commit()
    flash(
        "Η περίοδος άνοιξε ξανά. Η επόμενη οριστικοποίηση θα δημιουργήσει νέο snapshot.", "success"
    )
    return redirect(url_for("main.period_detail", period_id=period.id))


@main.route("/admin/users", methods=["GET", "POST"])
@login_required
@system_admin_required
def admin_users():
    generated_link = None
    if request.method == "POST":
        raw, token_hash, expires_at = issue_token(current_app.config["INVITATION_TTL_HOURS"])
        email = normalize_email(request.form.get("email", "")) or None
        db.session.add(
            Invitation(
                token_hash=token_hash,
                email=email,
                created_by_id=current_user.id,
                expires_at=expires_at,
            )
        )
        db.session.commit()
        generated_link = url_for("auth.register", token=raw, _external=True)
    users = db.session.scalars(db.select(User).order_by(User.email)).all()
    buildings = db.session.scalars(db.select(Building).order_by(Building.name)).all()
    memberships = db.session.scalars(
        db.select(BuildingMembership).join(Building).order_by(Building.name, BuildingMembership.id)
    ).all()
    memberships_by_user = {user.id: [] for user in users}
    for membership in memberships:
        memberships_by_user[membership.user_id].append(membership)
    return render_template(
        "admin/users.html",
        users=users,
        buildings=buildings,
        memberships_by_user=memberships_by_user,
        generated_link=generated_link,
    )


def other_active_admins(user):
    return db.session.scalar(
        db.select(db.func.count(User.id)).where(
            User.is_system_admin.is_(True),
            User.is_active_account.is_(True),
            User.id != user.id,
        )
    )


@main.post("/admin/users/<int:user_id>/permissions")
@login_required
@system_admin_required
def user_permissions(user_id):
    user = db.get_or_404(User, user_id)
    display_name = request.form.get("display_name", "").strip()
    email = normalize_email(request.form.get("email", ""))
    is_admin = request.form.get("user_type") == "admin"
    building_id = request.form.get("building_id", type=int)
    building = db.session.get(Building, building_id) if building_id else None
    is_self = user.id == current_user.id
    if building_id and not building:
        abort(400)
    if not display_name or "@" not in email:
        flash("Το ονοματεπώνυμο και ένα έγκυρο email είναι υποχρεωτικά.", "error")
    elif db.session.scalar(db.select(User).where(User.email == email, User.id != user.id)):
        flash("Το email χρησιμοποιείται ήδη από άλλον χρήστη.", "error")
    elif is_self and not is_admin:
        flash("Δεν μπορείτε να αφαιρέσετε από τον εαυτό σας τον ρόλο Administrator.", "error")
    elif (
        user.is_system_admin
        and not (is_admin and (is_self or request.form.get("is_active") == "on"))
        and not other_active_admins(user)
    ):
        flash("Πρέπει να υπάρχει πάντα τουλάχιστον ένας ενεργός Administrator.", "error")
    else:
        user.display_name = display_name
        user.email = email
        user.is_system_admin = is_admin
        user.is_active_account = is_self or request.form.get("is_active") == "on"
        # A simple user sees exactly one building (or none until one is assigned).
        db.session.execute(
            db.delete(BuildingMembership).where(BuildingMembership.user_id == user.id)
        )
        if not is_admin and building:
            db.session.add(
                BuildingMembership(user_id=user.id, building_id=building.id, role="viewer")
            )
        audit(
            "user_profile_changed",
            current_user.id,
            building.id if building else None,
            f"{user.email}: {'admin' if is_admin else 'user'}",
        )
        db.session.commit()
        flash("Τα στοιχεία του χρήστη ενημερώθηκαν.", "success")
    return redirect(url_for("main.admin_users"))


@main.get("/admin/database")
@login_required
@system_admin_required
def admin_database():
    database_path = application_database_path()
    stats = {
        "file_name": database_path.name,
        "size_mb": database_path.stat().st_size / (1024 * 1024),
        "users": db.session.scalar(db.select(db.func.count(User.id))),
        "buildings": db.session.scalar(db.select(db.func.count(Building.id))),
        "periods": db.session.scalar(db.select(db.func.count(Period.id))),
        "expenses": db.session.scalar(db.select(db.func.count(Expense.id))),
    }
    return render_template(
        "admin/database.html",
        stats=stats,
        backups=list_backups(),
        max_restore_mb=current_app.config["MAX_RESTORE_BYTES"] // (1024 * 1024),
    )


@main.post("/admin/database/backup")
@login_required
@system_admin_required
def admin_database_backup():
    try:
        audit("database_backup_requested", current_user.id)
        db.session.commit()
        backup_path = create_backup()
        response = send_file(
            backup_path,
            mimetype="application/vnd.sqlite3",
            as_attachment=True,
            download_name=backup_path.name,
        )
        response.headers["Cache-Control"] = "no-store"
        if request.headers.get("HX-Request") == "true":
            response.close()
            redirect_response = current_app.response_class("", status=200)
            redirect_response.headers["HX-Redirect"] = url_for(
                "main.admin_database_backup_download", filename=backup_path.name
            )
            return redirect_response
        return response
    except DatabaseMaintenanceError as exc:
        flash(f"Το backup απέτυχε: {exc}", "error")
        return redirect(url_for("main.admin_database"))


@main.post("/admin/database/backup/prepare")
@login_required
@system_admin_required
def admin_database_backup_prepare():
    try:
        audit("database_backup_requested", current_user.id)
        db.session.commit()
        backup_path = create_backup()
        return jsonify(
            filename=backup_path.name,
            download_url=url_for("main.admin_database_backup_download", filename=backup_path.name),
        )
    except DatabaseMaintenanceError as exc:
        return jsonify(error=f"Το backup απέτυχε: {exc}"), 400


@main.get("/admin/database/backups/fragment")
@login_required
@system_admin_required
def admin_database_backups_fragment():
    response = current_app.response_class(
        render_template("admin/_backup_history.html", backups=list_backups())
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@main.get("/admin/database/backups/<filename>")
@login_required
@system_admin_required
def admin_database_backup_download(filename):
    try:
        backup_path = local_backup_path(filename)
    except DatabaseMaintenanceError:
        abort(404)
    response = send_file(
        backup_path,
        mimetype="application/vnd.sqlite3",
        as_attachment=True,
        download_name=backup_path.name,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@main.post("/admin/database/backups/<filename>/delete")
@login_required
@system_admin_required
def admin_database_backup_delete(filename):
    try:
        deleted_name = delete_local_backup(filename)
        audit("database_backup_deleted", current_user.id, details=deleted_name)
        db.session.commit()
        message = f"Το backup «{deleted_name}» διαγράφηκε."
        if request.headers.get("HX-Request") == "true":
            return render_template(
                "admin/_backup_history.html",
                backups=list_backups(),
                backup_message=message,
            )
        flash(message, "success")
    except DatabaseMaintenanceError as exc:
        message = f"Η διαγραφή απέτυχε: {exc}"
        if request.headers.get("HX-Request") == "true":
            return render_template(
                "admin/_backup_history.html",
                backups=list_backups(),
                backup_error=message,
            )
        flash(message, "error")
    return redirect(url_for("main.admin_database"))


@main.post("/admin/database/restore")
@login_required
@system_admin_required
def admin_database_restore():
    password = request.form.get("password", "")
    confirmation = request.form.get("confirmation", "").strip()
    uploaded = request.files.get("database_file")
    if not current_user.check_password(password):
        flash("Ο κωδικός του admin δεν είναι σωστός.", "error")
        return redirect(url_for("main.admin_database"))
    if confirmation != "RESTORE":
        flash("Πληκτρολογήστε RESTORE για επιβεβαίωση.", "error")
        return redirect(url_for("main.admin_database"))
    if not uploaded or not uploaded.filename:
        flash("Επιλέξτε αρχείο SQLite backup.", "error")
        return redirect(url_for("main.admin_database"))

    admin_email = current_user.email
    try:
        upload_path = save_restore_upload(uploaded, current_app.config["MAX_RESTORE_BYTES"])
        audit("database_restore_requested", current_user.id, details=uploaded.filename)
        db.session.commit()
        pre_restore = restore_database(upload_path)
        restored_admin = db.session.scalar(
            db.select(User).where(
                User.email == admin_email,
                User.is_system_admin.is_(True),
            )
        ) or db.session.scalar(
            db.select(User).where(User.is_system_admin.is_(True)).order_by(User.id)
        )
        audit(
            "database_restored",
            restored_admin.id if restored_admin else None,
            details=f"Pre-restore backup: {pre_restore.name}",
        )
        db.session.commit()
    except DatabaseMaintenanceError as exc:
        flash(f"Το restore απέτυχε: {exc}", "error")
        return redirect(url_for("main.admin_database"))

    session.clear()
    flash("Η βάση επαναφέρθηκε επιτυχώς. Συνδεθείτε ξανά.", "success")
    return redirect(url_for("auth.login"))


@main.post("/admin/users/<int:user_id>/reset")
@login_required
@system_admin_required
def user_reset(user_id):
    user = db.get_or_404(User, user_id)
    for previous in db.session.scalars(
        db.select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
    ):
        previous.used_at = utcnow()
    raw, token_hash, expires_at = issue_token(current_app.config["RESET_TTL_HOURS"])
    db.session.add(
        PasswordResetToken(
            token_hash=token_hash,
            user_id=user.id,
            created_by_id=current_user.id,
            expires_at=expires_at,
        )
    )
    db.session.commit()
    link = url_for("auth.reset_password", token=raw, _external=True)
    flash(f"Password reset link για {user.email}: {link}", "link")
    return redirect(url_for("main.admin_users"))

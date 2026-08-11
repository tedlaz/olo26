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
    Expense,
    ExpenseCategory,
    Invitation,
    ManagerTerm,
    Millesimal,
    PasswordResetToken,
    Period,
    User,
    utcnow,
)
from .pdf_reports import create_period_report, create_receipts_report
from .permissions import membership_for, require_role, system_admin_required
from .security import issue_token, normalize_email
from .services import (
    BuildingWizardError,
    allocation_report,
    audit,
    build_allocations,
    create_building_graph,
    expense_report,
    replace_millesimals,
)

main = Blueprint("main", __name__)


def parse_date(value, label="ημερομηνία"):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Μη έγκυρη {label}.") from exc


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
def building_create():
    if not (current_user.is_system_admin or current_user.can_create_building):
        abort(403)
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
    eligible_users = db.session.scalars(
        db.select(User)
        .join(BuildingMembership)
        .where(BuildingMembership.building_id == building.id, User.is_active_account.is_(True))
        .order_by(User.email)
    ).all()
    return render_template(
        "building_detail.html",
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
    building = get_building(building_id, "editor")
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


@main.route("/buildings/<int:building_id>/members", methods=["GET", "POST"])
@login_required
def building_members(building_id):
    building = get_building(building_id, "building_admin")
    if request.method == "POST":
        user = db.session.get(User, request.form.get("user_id", type=int))
        role = request.form.get("role")
        if not user or role not in {"viewer", "editor", "building_admin"}:
            abort(400)
        membership = db.session.scalar(
            db.select(BuildingMembership).where(
                BuildingMembership.building_id == building.id,
                BuildingMembership.user_id == user.id,
            )
        )
        if membership:
            membership.role = role
        else:
            db.session.add(BuildingMembership(building_id=building.id, user_id=user.id, role=role))
        audit("building_member_changed", current_user.id, building.id, f"{user.email}: {role}")
        db.session.commit()
        flash("Η πρόσβαση ενημερώθηκε.", "success")
        return redirect(url_for("main.building_members", building_id=building.id))
    memberships = db.session.scalars(
        db.select(BuildingMembership)
        .where(BuildingMembership.building_id == building.id)
        .order_by(BuildingMembership.role, BuildingMembership.id)
    ).all()
    users = db.session.scalars(
        db.select(User).where(User.is_active_account.is_(True)).order_by(User.email)
    ).all()
    return render_template(
        "building_members.html", building=building, memberships=memberships, users=users
    )


@main.post("/buildings/<int:building_id>/apartments")
@login_required
def apartment_add(building_id):
    building = get_building(building_id, "editor")
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
        flash(f"Δεν προστέθηκε το διαμέρισμα: {exc}", "error")
    return redirect(url_for("main.building_detail", building_id=building.id))


@main.route("/apartments/<int:apartment_id>/edit", methods=["GET", "POST"])
@login_required
def apartment_edit(apartment_id):
    apartment = db.get_or_404(Apartment, apartment_id)
    require_role(apartment.building_id, "editor")
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
            return redirect(url_for("main.building_detail", building_id=apartment.building_id))
        except (ValueError, InvalidOperation, IntegrityError) as exc:
            db.session.rollback()
            flash(f"Δεν αποθηκεύτηκαν οι αλλαγές: {exc}", "error")
    return render_template("apartment_form.html", apartment=apartment)


@main.route("/buildings/<int:building_id>/categories/new", methods=["GET", "POST"])
@login_required
def category_add(building_id):
    building = get_building(building_id, "editor")
    apartments = sorted(building.apartments, key=lambda item: (item.number, item.id))
    if not apartments:
        flash("Προσθέστε πρώτα τα διαμερίσματα του κτιρίου.", "error")
        return redirect(url_for("main.building_detail", building_id=building.id))
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
            flash("Η κατηγορία προστέθηκε. Συμπληρώστε τα χιλιοστά της.", "success")
            return redirect(url_for("main.building_detail", building_id=building.id))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(f"Δεν προστέθηκε η κατηγορία: {exc}", "error")
    return render_template("category_form.html", building=building, apartments=apartments)


@main.route("/buildings/<int:building_id>/millesimals", methods=["GET", "POST"])
@login_required
def millesimals(building_id):
    building = get_building(building_id, "editor")
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
            return redirect(url_for("main.building_detail", building_id=building.id))
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
    building = get_building(building_id, "building_admin")
    user = db.session.get(User, int(request.form.get("user_id", 0)))
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
            return redirect(url_for("main.building_detail", building_id=building.id))
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
    return redirect(url_for("main.building_detail", building_id=building.id))


@main.route("/buildings/<int:building_id>/periods/new", methods=["GET", "POST"])
@login_required
def period_create(building_id):
    building = get_building(building_id, "editor")
    if request.method == "POST":
        try:
            issue_date = parse_date(request.form.get("issue_date"), "ημερομηνία έκδοσης")
            manager = db.session.scalar(
                db.select(ManagerTerm).where(
                    ManagerTerm.building_id == building.id,
                    ManagerTerm.date_from <= issue_date,
                    or_(ManagerTerm.date_to.is_(None), ManagerTerm.date_to >= issue_date),
                )
            )
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
            flash(f"Δεν δημιουργήθηκε η περίοδος: {exc}", "error")
    return render_template("period_form.html", building=building)


@main.route("/periods/<int:period_id>/edit", methods=["GET", "POST"])
@login_required
def period_edit(period_id):
    period = db.get_or_404(Period, period_id)
    require_role(period.building_id, "editor")
    if period.status != "draft":
        abort(409)
    if request.method == "POST":
        try:
            issue_date = parse_date(request.form.get("issue_date"), "ημερομηνία έκδοσης")
            manager = db.session.scalar(
                db.select(ManagerTerm).where(
                    ManagerTerm.building_id == period.building_id,
                    ManagerTerm.date_from <= issue_date,
                    or_(ManagerTerm.date_to.is_(None), ManagerTerm.date_to >= issue_date),
                )
            )
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
            flash(f"Δεν αποθηκεύτηκαν οι αλλαγές: {exc}", "error")
    return render_template("period_edit_form.html", period=period)


@main.get("/periods/<int:period_id>")
@login_required
def period_detail(period_id):
    period = db.get_or_404(Period, period_id)
    role = require_role(period.building_id)
    if role == "viewer" and period.status != "finalized":
        abort(403)
    return render_template(
        "period_detail.html",
        period=period,
        role=role,
        expense_report=expense_report(period),
        allocation_report=allocation_report(period) if period.status == "finalized" else None,
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
        as_attachment=True,
        download_name=f"koinoxrista-{period.issue_date.isoformat()}.pdf",
    )


@main.get("/periods/<int:period_id>/receipts.pdf")
@login_required
def period_receipts_pdf(period_id):
    period = finalized_period_for_report(period_id)
    return send_file(
        BytesIO(create_receipts_report(period)),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"apodeixeis-{period.issue_date.isoformat()}.pdf",
    )


@main.post("/periods/<int:period_id>/expenses")
@login_required
def expense_add(period_id):
    period = db.get_or_404(Period, period_id)
    require_role(period.building_id, "editor")
    if period.status != "draft":
        abort(409)
    try:
        expense = Expense(period=period)
        update_expense_from_form(expense, period)
        db.session.add(expense)
        db.session.commit()
        flash("Η δαπάνη προστέθηκε.", "success")
    except (ValueError, InvalidOperation, IntegrityError) as exc:
        db.session.rollback()
        flash(f"Δεν προστέθηκε η δαπάνη: {exc}", "error")
    return redirect(url_for("main.period_detail", period_id=period.id))


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

    invoice_number = request.form.get("invoice_number", "").strip()
    description = request.form.get("description", "").strip()
    if not invoice_number or not description:
        raise ValueError("Ο αριθμός και η περιγραφή είναι υποχρεωτικά.")

    expense.category = category
    expense.invoice_date = parse_date(request.form.get("invoice_date"))
    expense.invoice_number = invoice_number
    expense.description = description
    expense.amount = Decimal(request.form.get("amount", ""))


@main.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def expense_edit(expense_id):
    expense = db.get_or_404(Expense, expense_id)
    period = expense.period
    require_role(period.building_id, "editor")
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
            flash(f"Δεν αποθηκεύτηκαν οι αλλαγές: {exc}", "error")
    return render_template("expense_form.html", expense=expense, period=period)


@main.post("/expenses/<int:expense_id>/delete")
@login_required
def expense_delete(expense_id):
    expense = db.get_or_404(Expense, expense_id)
    period = expense.period
    require_role(period.building_id, "editor")
    if period.status != "draft":
        abort(409)
    db.session.delete(expense)
    audit("expense_deleted", current_user.id, period.building_id, str(expense.id))
    db.session.commit()
    flash("Η δαπάνη διαγράφηκε.", "success")
    return redirect(url_for("main.period_detail", period_id=period.id))


@main.post("/periods/<int:period_id>/finalize")
@login_required
def period_finalize(period_id):
    period = db.get_or_404(Period, period_id)
    require_role(period.building_id, "editor")
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
    require_role(period.building_id, "building_admin")
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
        access_labels={
            "viewer": "Μόνο προβολή",
            "editor": "Προβολή και αλλαγές",
            "building_admin": "Πλήρης διαχείριση",
        },
        generated_link=generated_link,
    )


@main.post("/admin/users/<int:user_id>/permissions")
@login_required
@system_admin_required
def user_permissions(user_id):
    user = db.get_or_404(User, user_id)
    display_name = request.form.get("display_name", "").strip()
    email = user.email if user.is_system_admin else normalize_email(request.form.get("email", ""))
    if not display_name or "@" not in email:
        flash("Το ονοματεπώνυμο και ένα έγκυρο email είναι υποχρεωτικά.", "error")
        return redirect(url_for("main.admin_users"))
    duplicate = db.session.scalar(db.select(User).where(User.email == email, User.id != user.id))
    if duplicate:
        flash("Το email χρησιμοποιείται ήδη από άλλον χρήστη.", "error")
        return redirect(url_for("main.admin_users"))
    user.display_name = display_name
    user.email = email
    if not user.is_system_admin:
        user.can_create_building = request.form.get("can_create_building") == "on"
        user.is_active_account = request.form.get("is_active") == "on"
    audit("user_profile_changed", current_user.id, details=user.email)
    db.session.commit()
    flash("Τα στοιχεία και τα καθολικά δικαιώματα ενημερώθηκαν.", "success")
    return redirect(url_for("main.admin_users"))


@main.post("/admin/users/<int:user_id>/building-access")
@login_required
@system_admin_required
def user_building_access(user_id):
    user = db.get_or_404(User, user_id)
    if user.is_system_admin:
        abort(400, description="Ο system admin έχει ήδη πρόσβαση σε όλα τα κτίρια.")
    building_id = request.form.get("building_id", type=int)
    role = request.form.get("role")
    building = db.session.get(Building, building_id)
    if not building or role not in {"viewer", "editor", "building_admin"}:
        abort(400)
    membership = db.session.scalar(
        db.select(BuildingMembership).where(
            BuildingMembership.user_id == user.id,
            BuildingMembership.building_id == building.id,
        )
    )
    if membership:
        membership.role = role
    else:
        db.session.add(BuildingMembership(user_id=user.id, building_id=building.id, role=role))
    audit(
        "user_building_access_changed",
        current_user.id,
        building.id,
        f"{user.email}: {role}",
    )
    db.session.commit()
    flash(f"Η πρόσβαση στο «{building.name}» ενημερώθηκε.", "success")
    return redirect(url_for("main.admin_users"))


@main.post("/admin/users/<int:user_id>/building-access/<int:building_id>/delete")
@login_required
@system_admin_required
def user_building_access_delete(user_id, building_id):
    user = db.get_or_404(User, user_id)
    building = db.get_or_404(Building, building_id)
    membership = db.session.scalar(
        db.select(BuildingMembership).where(
            BuildingMembership.user_id == user.id,
            BuildingMembership.building_id == building.id,
        )
    )
    if membership:
        db.session.delete(membership)
        audit(
            "user_building_access_removed",
            current_user.id,
            building.id,
            user.email,
        )
        db.session.commit()
        flash(f"Η πρόσβαση στο «{building.name}» αφαιρέθηκε.", "success")
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

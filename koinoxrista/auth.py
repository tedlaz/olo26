from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .extensions import db
from .models import Invitation, PasswordResetToken, User, utcnow
from .security import hash_token, normalize_email, valid_password

auth = Blueprint("auth", __name__, url_prefix="/auth")


@auth.before_app_request
def require_changed_password():
    if not current_user.is_authenticated or not current_user.must_change_password:
        return None
    allowed = {"auth.change_password", "auth.logout", "static"}
    if request.endpoint not in allowed:
        return redirect(url_for("auth.change_password"))
    return None


@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        user = db.session.scalar(db.select(User).where(User.email == email))
        if user and user.is_active and user.check_password(request.form.get("password", "")):
            login_user(user)
            return redirect(
                url_for("auth.change_password")
                if user.must_change_password
                else url_for("main.dashboard")
            )
        flash("Λανθασμένο email ή password.", "error")
    return render_template("auth/login.html")


@auth.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        password = request.form.get("password", "")
        if not current_user.check_password(current):
            flash("Ο τρέχων κωδικός δεν είναι σωστός.", "error")
        elif not valid_password(password):
            flash("Ο νέος κωδικός πρέπει να έχει τουλάχιστον 10 χαρακτήρες.", "error")
        elif password != request.form.get("confirm_password"):
            flash("Η επιβεβαίωση του κωδικού δεν συμφωνεί.", "error")
        else:
            current_user.set_password(password)
            current_user.must_change_password = False
            current_user.auth_version += 1
            db.session.commit()
            login_user(current_user, fresh=True)
            flash("Ο κωδικός άλλαξε.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/change_password.html")


@auth.route("/register/<token>", methods=["GET", "POST"])
def register(token):
    invitation = db.session.scalar(
        db.select(Invitation).where(Invitation.token_hash == hash_token(token))
    )
    if not invitation or invitation.used_at or invitation.expires_at < utcnow():
        return render_template("auth/token_invalid.html"), 400
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        if invitation.email and email != invitation.email:
            flash("Η πρόσκληση προορίζεται για διαφορετικό email.", "error")
        elif db.session.scalar(db.select(User).where(User.email == email)):
            flash("Υπάρχει ήδη χρήστης με αυτό το email.", "error")
        elif "@" not in email:
            flash("Χρειάζεται έγκυρο email.", "error")
        elif not display_name:
            flash("Το ονοματεπώνυμο είναι υποχρεωτικό.", "error")
        elif not valid_password(password):
            flash("Το password πρέπει να έχει τουλάχιστον 10 χαρακτήρες.", "error")
        elif password != request.form.get("confirm_password"):
            flash("Η επιβεβαίωση του password δεν συμφωνεί.", "error")
        else:
            user = User(email=email, display_name=display_name)
            user.set_password(password)
            invitation.used_at = utcnow()
            db.session.add(user)
            db.session.commit()
            flash("Η εγγραφή ολοκληρώθηκε. Ο admin μπορεί τώρα να σας δώσει πρόσβαση.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/register.html", invitation=invitation)


@auth.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    item = db.session.scalar(
        db.select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token))
    )
    if not item or item.used_at or item.expires_at < utcnow():
        return render_template("auth/token_invalid.html"), 400
    if request.method == "POST":
        password = request.form.get("password", "")
        if not valid_password(password):
            flash("Το password πρέπει να έχει τουλάχιστον 10 χαρακτήρες.", "error")
        elif password != request.form.get("confirm_password"):
            flash("Η επιβεβαίωση του password δεν συμφωνεί.", "error")
        else:
            item.user.set_password(password)
            item.user.must_change_password = False
            item.user.auth_version += 1
            item.used_at = utcnow()
            db.session.commit()
            flash("Το password επαναφέρθηκε.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", item=item)

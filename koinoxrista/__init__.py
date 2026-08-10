import os
from pathlib import Path

import click
from flask import Flask, request, session, url_for
from flask_login import current_user
from sqlalchemy import inspect, text

from .extensions import csrf, db, login_manager
from .models import Apartment, Building, Expense, Period, User


def configured_secret_key():
    secret_file = os.environ.get("SECRET_KEY_FILE")
    if secret_file:
        try:
            secret = Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"Unable to read SECRET_KEY_FILE: {exc}") from exc
        if len(secret) < 32:
            raise RuntimeError("SECRET_KEY_FILE must contain at least 32 characters.")
        return secret
    environment_secret = os.environ.get("SECRET_KEY")
    if environment_secret:
        return environment_secret
    if os.environ.get("APP_ENV", "").strip().lower() == "production":
        raise RuntimeError("Production requires SECRET_KEY_FILE or SECRET_KEY.")
    return "dev-only-change-me"


def environment_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def initialize_database():
    """Create or safely upgrade the application database."""
    db.create_all()
    user_columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
    if "display_name" not in user_columns:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN display_name VARCHAR(100) NOT NULL DEFAULT ''")
        )
        db.session.execute(
            text(
                "UPDATE users SET display_name = "
                "CASE WHEN email = 'admin@admin.app' THEN 'Administrator' ELSE email END"
            )
        )
        db.session.commit()

    building_columns = {column["name"] for column in inspect(db.engine).get_columns("buildings")}
    if "apartment_display_mode" not in building_columns:
        db.session.execute(
            text(
                "ALTER TABLE buildings ADD COLUMN apartment_display_mode "
                "VARCHAR(20) NOT NULL DEFAULT 'occupant'"
            )
        )
        db.session.commit()

    from .data_migrations import drop_period_legacy_id, normalize_legacy_period_ids

    normalize_legacy_period_ids()
    drop_period_legacy_id()

    admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
    created_admin = admin is None
    if admin is None:
        admin = User(
            email="admin@admin.app",
            display_name="Administrator",
            is_system_admin=True,
            can_create_building=True,
            must_change_password=True,
        )
        admin.set_password("changeme")
        db.session.add(admin)
        db.session.commit()
    if not admin.display_name:
        admin.display_name = "Administrator"
    from .legacy import materialize_legacy_managers

    materialize_legacy_managers()
    db.session.commit()
    return created_admin


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=configured_secret_key(),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", f"sqlite:///{Path(app.instance_path) / 'koinoxrista_app.db'}"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=environment_flag("COOKIE_SECURE"),
        LEGACY_DATABASE=str(Path(app.root_path).parent / "koinoxrista.db"),
        INVITATION_TTL_HOURS=72,
        RESET_TTL_HOURS=2,
        MAX_RESTORE_BYTES=200 * 1024 * 1024,
        AUTO_INIT_DATABASE=True,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Χρειάζεται να συνδεθείτε."

    from .auth import auth
    from .main import main

    app.register_blueprint(auth)
    app.register_blueprint(main)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        return response

    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            db.session.rollback()
            return {"status": "unhealthy"}, 503
        return {"status": "ok"}

    if app.config["AUTO_INIT_DATABASE"]:
        with app.app_context():
            initialize_database()

    @login_manager.user_loader
    def load_user(user_id):
        try:
            raw_id, raw_version = user_id.split(":", 1)
            user = db.session.get(User, int(raw_id))
            if user and user.auth_version == int(raw_version):
                return user
        except (AttributeError, TypeError, ValueError):
            return None
        return None

    @app.context_processor
    def inject_back_navigation():
        if not current_user.is_authenticated or not request.endpoint:
            return {}

        endpoint = request.endpoint
        view_args = request.view_args or {}
        if endpoint == "main.dashboard" or endpoint.startswith("auth."):
            return {}
        if endpoint in {
            "main.admin_users",
            "main.admin_database",
            "main.building_create",
            "main.initial_setup",
        }:
            return {
                "back_navigation": {
                    "url": url_for("main.dashboard"),
                    "label": "Πίσω στα κτίρια",
                }
            }
        if endpoint == "main.building_detail":
            return {
                "back_navigation": {
                    "url": url_for("main.dashboard"),
                    "label": "Πίσω στα κτίρια",
                }
            }

        building = None
        building_id = view_args.get("building_id")
        if building_id:
            building = db.session.get(Building, building_id)
        period_id = view_args.get("period_id")
        if period_id:
            period = db.session.get(Period, period_id)
            if endpoint == "main.period_edit" and period:
                return {
                    "back_navigation": {
                        "url": url_for("main.period_detail", period_id=period.id),
                        "label": "Πίσω στην περίοδο",
                    }
                }
            building = period.building if period else None
        apartment_id = view_args.get("apartment_id")
        if apartment_id:
            apartment = db.session.get(Apartment, apartment_id)
            building = apartment.building if apartment else None
        expense_id = view_args.get("expense_id")
        if expense_id:
            expense = db.session.get(Expense, expense_id)
            if expense:
                return {
                    "back_navigation": {
                        "url": url_for("main.period_detail", period_id=expense.period_id),
                        "label": "Πίσω στην περίοδο",
                    }
                }
        if building:
            page = session.get(f"building_page_{building.id}", 1)
            return {
                "back_navigation": {
                    "url": url_for("main.building_detail", building_id=building.id, page=page),
                    "label": f"Πίσω στο {building.name}",
                }
            }
        return {
            "back_navigation": {
                "url": url_for("main.dashboard"),
                "label": "Πίσω στα κτίρια",
            }
        }

    @app.cli.command("init-app")
    def init_app_command():
        """Create the new database and seed the system administrator."""
        if initialize_database():
            click.echo("Created admin@admin.app with the required temporary password.")
        else:
            click.echo("Application database already initialized.")

    return app

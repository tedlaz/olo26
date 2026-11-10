from functools import wraps

from flask import abort
from flask_login import current_user

from .extensions import db
from .models import BuildingMembership


def membership_for(building_id):
    """'admin' for administrators, 'viewer' for users with access to the building, else None."""
    if current_user.is_system_admin:
        return "admin"
    membership = db.session.scalar(
        db.select(BuildingMembership).where(
            BuildingMembership.building_id == building_id,
            BuildingMembership.user_id == current_user.id,
        )
    )
    return "viewer" if membership else None


def require_role(building_id, minimum="viewer"):
    role = membership_for(building_id)
    if role is None or (minimum == "admin" and role != "admin"):
        abort(403)
    return role


def system_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_system_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped

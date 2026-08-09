from functools import wraps

from flask import abort
from flask_login import current_user

from .extensions import db
from .models import BuildingMembership

ROLE_LEVEL = {"viewer": 1, "editor": 2, "building_admin": 3}


def membership_for(building_id):
    if current_user.is_system_admin:
        return "building_admin"
    membership = db.session.scalar(
        db.select(BuildingMembership).where(
            BuildingMembership.building_id == building_id,
            BuildingMembership.user_id == current_user.id,
        )
    )
    return membership.role if membership else None


def require_role(building_id, minimum="viewer"):
    role = membership_for(building_id)
    if role is None or ROLE_LEVEL[role] < ROLE_LEVEL[minimum]:
        abort(403)
    return role


def system_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_system_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped

import hashlib
import secrets
from datetime import timedelta

from .models import utcnow


def normalize_email(value):
    return value.strip().casefold()


def issue_token(hours):
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest(), utcnow() + timedelta(hours=hours)


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def valid_password(password):
    return len(password) >= 10

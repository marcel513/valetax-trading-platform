import hashlib
import secrets
from datetime import datetime, timedelta, timezone


def utcnow():
    return datetime.now(timezone.utc)


def stamp(dt):
    return dt.isoformat(timespec="seconds")


def parse(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def token(n=32):
    return secrets.token_urlsafe(n)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def expires(minutes):
    return stamp(utcnow() + timedelta(minutes=minutes))

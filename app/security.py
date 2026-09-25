"""Passwords, sessions and attempt limits for the admin."""

import base64
import hashlib
import hmac
import json
import secrets
import time

import bcrypt

from app.store import get_store

ACCOUNT_KEY = "admin"
SESSION_SECONDS = 12 * 3600
MIN_PASSWORD_LENGTH = 10


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


# ---- Password --------------------------------------------------------------

def hash_password(password: str) -> str:
    # bcrypt only uses the first 72 bytes; cut explicitly (bcrypt 5 raises otherwise).
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


# A real hash to compare against when no account exists, so timing doesn't
# reveal whether a password has been set.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=12)).decode()


def check_password(password: str, stored_hash: str | None) -> bool:
    return bcrypt.checkpw(password.encode()[:72], (stored_hash or _DUMMY_HASH).encode())


def emails_match(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(given.strip().lower(), expected)


# ---- Account ---------------------------------------------------------------
# Stored as a Redis hash: {hash, secret, version}. `secret` signs sessions and
# `version` changes with every password change, which signs out old sessions.

def get_account() -> dict | None:
    account = get_store().hgetall(ACCOUNT_KEY) or {}
    account = {_text(k): _text(v) for k, v in account.items()}
    return account if account.get("hash") else None


def save_password(password: str) -> None:
    previous = get_account() or {}
    get_store().hset(
        ACCOUNT_KEY,
        values={
            "hash": hash_password(password),
            "secret": f"s_{secrets.token_hex(32)}",
            "version": str(int(previous.get("version", 0)) + 1),
        },
    )


# ---- Sessions --------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def create_session(account: dict) -> str:
    payload = _b64(json.dumps({"exp": int(time.time()) + SESSION_SECONDS, "v": account["version"]}).encode())
    return f"sess.{payload}.{_sign(account['secret'], payload)}"


def verify_session(token: str) -> bool:
    parts = (token or "").split(".")
    if len(parts) != 3 or parts[0] != "sess":
        return False
    _, payload, signature = parts
    account = get_account()
    if not account or not hmac.compare_digest(signature, _sign(account["secret"], payload)):
        return False
    try:
        data = json.loads(_unb64(payload))
    except ValueError:
        return False
    return data.get("exp", 0) > time.time() and str(data.get("v")) == account["version"]


# ---- Attempt limits --------------------------------------------------------

def is_limited(bucket: str, ip: str, per_ip: int, total: int) -> bool:
    store = get_store()
    return (
        int(store.get(f"limit:{bucket}:{ip}") or 0) >= per_ip
        or int(store.get(f"limit:{bucket}:all") or 0) >= total
    )


def count_attempt(bucket: str, ip: str, window_seconds: int) -> None:
    store = get_store()
    for key in (f"limit:{bucket}:{ip}", f"limit:{bucket}:all"):
        if store.incr(key) == 1:
            store.expire(key, window_seconds)


def reset_attempts(bucket: str, ip: str) -> None:
    get_store().delete(f"limit:{bucket}:{ip}")

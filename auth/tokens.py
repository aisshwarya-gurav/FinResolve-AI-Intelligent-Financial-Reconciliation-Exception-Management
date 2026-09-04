"""JWT access tokens. Secret and algorithm come from the environment only."""

import os
from datetime import datetime, timedelta, timezone

import jwt

from .models import Role

_DEFAULT_EXPIRE_MINUTES = 60
_DEFAULT_ALGORITHM = "HS256"


class TokenError(Exception):
    pass


def _secret_key() -> str:
    key = os.environ.get("JWT_SECRET_KEY")
    if not key:
        raise TokenError("JWT_SECRET_KEY is not set.")
    return key


def _algorithm() -> str:
    return os.environ.get("JWT_ALGORITHM") or _DEFAULT_ALGORITHM


def _expire_minutes() -> int:
    raw = os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    if not raw:
        return _DEFAULT_EXPIRE_MINUTES
    try:
        return int(raw)
    except ValueError as e:
        raise TokenError("JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be an integer.") from e


def create_access_token(user_id: str, role: Role | str, expires_delta: timedelta | None = None) -> str:
    """Encode user_id, role, and expiry only — never a password or secret."""
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=_expire_minutes()))
    payload = {
        "user_id": user_id,
        "role": role.value if isinstance(role, Role) else str(role),
        "exp": expire,
    }
    return jwt.encode(payload, _secret_key(), algorithm=_algorithm())


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[_algorithm()])
    except jwt.ExpiredSignatureError as e:
        raise TokenError("Token has expired.") from e
    except jwt.InvalidTokenError as e:
        raise TokenError("Invalid token.") from e

    user_id = payload.get("user_id")
    role = payload.get("role")
    if not user_id or not role:
        raise TokenError("Invalid token.")
    return {"user_id": user_id, "role": role, "exp": payload.get("exp")}

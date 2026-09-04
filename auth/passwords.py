"""Password hashing — bcrypt only. Never log the plaintext or the hash."""

import bcrypt

_MIN_LENGTH = 8


class PasswordError(ValueError):
    pass


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < _MIN_LENGTH:
        raise PasswordError(f"Password must be at least {_MIN_LENGTH} characters.")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False

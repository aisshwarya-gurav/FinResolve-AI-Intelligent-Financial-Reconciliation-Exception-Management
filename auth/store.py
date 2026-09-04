"""Users table on the existing Case Management SQLite file.

CREATE TABLE IF NOT EXISTS — never drops cases, notes, or audit_events.
"""

import sqlite3
import uuid
from typing import Optional

from .models import Role, User
from .passwords import hash_password, PasswordError
from case_management.models import now_iso

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
"""


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class InvalidRoleError(Exception):
    pass


class UserStore:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(USERS_SCHEMA)
        self.conn.commit()

    def create_user(
        self,
        email: str,
        password: str,
        display_name: str,
        role: Role | str,
        is_active: bool = True,
        user_id: Optional[str] = None,
    ) -> User:
        try:
            role_enum = role if isinstance(role, Role) else Role(role)
        except ValueError as e:
            raise InvalidRoleError(f"Invalid role '{role}'.") from e

        email_norm = _normalize_email(email)
        if not email_norm:
            raise ValueError("Email is required.")
        if not display_name or not display_name.strip():
            raise ValueError("display_name is required.")

        try:
            password_hash = hash_password(password)
        except PasswordError:
            raise

        uid = user_id or uuid.uuid4().hex
        ts = now_iso()
        try:
            self.conn.execute(
                "INSERT INTO users (user_id, email, display_name, password_hash, role, is_active, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, email_norm, display_name.strip(), password_hash, role_enum.value, 1 if is_active else 0, ts),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            raise UserAlreadyExistsError(f"A user with email '{email_norm}' already exists.") from e
        return self.get_by_id(uid)

    def get_by_id(self, user_id: str) -> User:
        row = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise UserNotFoundError(user_id)
        return _row_to_user(row)

    def get_by_email(self, email: str) -> User:
        row = self.conn.execute(
            "SELECT * FROM users WHERE email = ?", (_normalize_email(email),)
        ).fetchone()
        if row is None:
            raise UserNotFoundError(email)
        return _row_to_user(row)

    def set_last_login(self, user_id: str) -> None:
        self.conn.execute(
            "UPDATE users SET last_login_at = ? WHERE user_id = ?",
            (now_iso(), user_id),
        )
        self.conn.commit()


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        user_id=row["user_id"],
        email=row["email"],
        display_name=row["display_name"],
        password_hash=row["password_hash"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )

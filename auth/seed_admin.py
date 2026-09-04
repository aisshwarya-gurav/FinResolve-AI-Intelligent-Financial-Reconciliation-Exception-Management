"""
Create the first ADMIN user. Never runs automatically on API startup.

Requires AUTH_ADMIN_EMAIL and AUTH_ADMIN_PASSWORD in the environment
(or a local .env you load yourself). No default password is used.

  python -m auth.seed_admin
"""

import os

from .models import Role
from .passwords import PasswordError
from .store import UserAlreadyExistsError, UserStore, UserNotFoundError


def seed_admin(db_path: str | None = None) -> str:
    email = os.environ.get("AUTH_ADMIN_EMAIL", "").strip()
    password = os.environ.get("AUTH_ADMIN_PASSWORD", "")
    display_name = os.environ.get("AUTH_ADMIN_DISPLAY_NAME", "").strip() or "Administrator"

    if not email or not password:
        raise SystemExit(
            "Set AUTH_ADMIN_EMAIL and AUTH_ADMIN_PASSWORD before running "
            "python -m auth.seed_admin. No default admin password is provided."
        )

    if db_path is None:
        db_path = os.environ.get("CASE_DB_PATH") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "case_management", "cases.db")
        )

    store = UserStore(db_path)
    try:
        user = store.create_user(
            email=email,
            password=password,
            display_name=display_name,
            role=Role.ADMIN,
        )
    except UserAlreadyExistsError:
        raise SystemExit(f"An account for {email.lower()} already exists. No changes made.")
    except (PasswordError, ValueError) as e:
        raise SystemExit(str(e))
    return user.user_id


def seed_demo_roles(db_path: str | None = None) -> list[str]:
    """Create the standard demo RBAC accounts only if they do not already exist."""
    if db_path is None:
        db_path = os.environ.get("CASE_DB_PATH") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "case_management", "cases.db")
        )

    store = UserStore(db_path)
    demo_accounts = [
        ("manager@razorpay.local", Role.FINANCE_MANAGER, "Finance Manager"),
        ("analyst@razorpay.local", Role.FINANCE_ANALYST, "Finance Analyst"),
        ("auditor@razorpay.local", Role.AUDITOR, "Auditor"),
        ("viewer@razorpay.local", Role.VIEWER, "Viewer"),
    ]

    created: list[str] = []
    for email, role, display_name in demo_accounts:
        try:
            store.get_by_email(email)
        except UserNotFoundError:
            try:
                store.create_user(
                    email=email,
                    password="RazorPay@123",
                    display_name=display_name,
                    role=role,
                )
                created.append(email)
            except UserAlreadyExistsError:
                continue
    return created


def main() -> None:
    user_id = seed_admin()
    # Never print the password.
    print(f"ADMIN user created (user_id={user_id}).")


if __name__ == "__main__":
    main()

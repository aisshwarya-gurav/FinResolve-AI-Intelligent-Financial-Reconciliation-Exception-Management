"""Identity and permission types for RBAC."""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class Role(str, Enum):
    ADMIN = "ADMIN"
    FINANCE_MANAGER = "FINANCE_MANAGER"
    FINANCE_ANALYST = "FINANCE_ANALYST"
    AUDITOR = "AUDITOR"
    VIEWER = "VIEWER"


class Permission(str, Enum):
    VIEW_CASES = "VIEW_CASES"
    RUN_RECONCILIATION = "RUN_RECONCILIATION"
    INVESTIGATE_CASE = "INVESTIGATE_CASE"
    ADD_NOTE = "ADD_NOTE"
    ASSIGN_CASE = "ASSIGN_CASE"
    CHANGE_STATUS = "CHANGE_STATUS"
    RESOLVE_CASE = "RESOLVE_CASE"
    VIEW_AUDIT = "VIEW_AUDIT"
    EXPORT_DATA = "EXPORT_DATA"
    CONFIGURE_RAZORPAY = "CONFIGURE_RAZORPAY"
    TRIGGER_RAZORPAY = "TRIGGER_RAZORPAY"
    MANAGE_USERS = "MANAGE_USERS"


@dataclass
class User:
    user_id: str
    email: str
    display_name: str
    password_hash: str
    role: str
    is_active: bool
    created_at: str
    last_login_at: Optional[str] = None

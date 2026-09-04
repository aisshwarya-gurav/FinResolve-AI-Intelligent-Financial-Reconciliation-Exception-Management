"""Least-privilege role → permission mapping."""

from .models import Role, Permission, User

_ALL = set(Permission)

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: _ALL,
    Role.FINANCE_MANAGER: {
        Permission.VIEW_CASES,
        Permission.RUN_RECONCILIATION,
        Permission.INVESTIGATE_CASE,
        Permission.ADD_NOTE,
        Permission.ASSIGN_CASE,
        Permission.CHANGE_STATUS,
        Permission.RESOLVE_CASE,
        Permission.VIEW_AUDIT,
        Permission.EXPORT_DATA,
        Permission.TRIGGER_RAZORPAY,
    },
    Role.FINANCE_ANALYST: {
        Permission.VIEW_CASES,
        Permission.INVESTIGATE_CASE,
        Permission.ADD_NOTE,
        Permission.CHANGE_STATUS,
        Permission.VIEW_AUDIT,
        Permission.EXPORT_DATA,
    },
    Role.AUDITOR: {
        Permission.VIEW_CASES,
        Permission.ADD_NOTE,
        Permission.VIEW_AUDIT,
        Permission.EXPORT_DATA,
    },
    Role.VIEWER: {
        Permission.VIEW_CASES,
    },
}


def permissions_for_role(role: Role | str) -> set[Permission]:
    try:
        role_enum = role if isinstance(role, Role) else Role(role)
    except ValueError:
        return set()
    return ROLE_PERMISSIONS.get(role_enum, set())


def has_permission(role: Role | str, permission: Permission) -> bool:
    return permission in permissions_for_role(role)


def user_has_permission(user: User, permission: Permission) -> bool:
    return has_permission(user.role, permission)

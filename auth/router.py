"""Auth HTTP routes: login and (admin) user creation."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from .deps import require_permission
from .models import Permission, User
from .passwords import PasswordError, verify_password
from .store import InvalidRoleError, UserAlreadyExistsError, UserNotFoundError
from .tokens import TokenError, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])

_LOGIN_FAILED = "Invalid email or password"


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateUserRequest(BaseModel):
    email: str
    password: str
    display_name: str
    role: str
    is_active: bool = True


def _public_user(user: User) -> dict:
    data = asdict(user)
    data.pop("password_hash", None)
    return data


@router.post("/login")
def login(body: LoginRequest, request: Request):
    user_store = request.app.state.user_store
    try:
        user = user_store.get_by_email(body.email)
    except UserNotFoundError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_LOGIN_FAILED)

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive.")

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_LOGIN_FAILED)

    try:
        token = create_access_token(user.user_id, user.role)
    except TokenError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e

    user_store.set_last_login(user.user_id)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserRequest,
    request: Request,
    _: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    user_store = request.app.state.user_store
    try:
        user = user_store.create_user(
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            role=body.role,
            is_active=body.is_active,
        )
    except UserAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except (InvalidRoleError, PasswordError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _public_user(user)

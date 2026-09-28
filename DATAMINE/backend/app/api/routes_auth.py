from datetime import datetime, timezone
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, hash_password, verify_password, get_current_user, require_roles
from app.db.session import get_db
from app.models.identity import AuditLog, User

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    confirm_password: str | None = Field(default=None, min_length=12, max_length=256)
    full_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.confirm_password is not None and self.password != self.confirm_password:
            raise ValueError("Password and confirmation do not match")
        return self


class UserProvisionRequest(RegisterRequest):
    role: str = Field(pattern="^(ADMIN|VERIFIER|ANALYST|VIEWER)$")


@router.post("/register", status_code=201)
async def register(request: RegisterRequest, session: AsyncSession = Depends(get_db)) -> dict:
    if request.confirm_password is None:
        raise HTTPException(422, "Password confirmation is required")
    if await session.scalar(select(User.id).where((User.username == request.username) | (User.email == request.email))):
        raise HTTPException(409, "Username or email is already registered")
    user = User(username=request.username, email=request.email, password_hash=hash_password(request.password), full_name=request.full_name, role="VIEWER")
    session.add(user)
    await session.flush()
    session.add(AuditLog(actor_id=user.id, action="VIEWER_REGISTERED", entity_type="USER", entity_id=user.id, details={"role": "VIEWER"}, source="auth_api"))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Username or email is already registered") from exc
    return {"id": user.id, "username": user.username, "role": user.role, "is_active": user.is_active, "created_at": user.created_at}


@router.post("/users", status_code=201)
async def provision_user(request: UserProvisionRequest, actor: User = Depends(require_roles("ADMIN")), session: AsyncSession = Depends(get_db)) -> dict:
    if await session.scalar(select(User.id).where((User.username == request.username) | (User.email == request.email))):
        raise HTTPException(409, "Username or email is already registered")
    user = User(username=request.username, email=request.email, password_hash=hash_password(request.password), full_name=request.full_name, role=request.role)
    session.add(user)
    await session.flush()
    session.add(AuditLog(actor_id=actor.id, action="CREATE", entity_type="USER", entity_id=user.id, details={"role": user.role}))
    await session.commit()
    return {"id": user.id, "username": user.username, "role": user.role, "created_at": user.created_at}


class ViewerStatusRequest(BaseModel):
    is_active: bool


def _viewer_account(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "last_login": user.last_login,
    }


@router.get("/users/viewers")
async def list_viewers(actor: User = Depends(require_roles("ADMIN")), session: AsyncSession = Depends(get_db)) -> dict:
    users = (await session.execute(select(User).where(User.role == "VIEWER").order_by(User.created_at.desc(), User.id))).scalars().all()
    return {"items": [_viewer_account(user) for user in users], "total": len(users)}


@router.get("/users/viewers/{user_id}")
async def get_viewer(user_id: uuid.UUID, actor: User = Depends(require_roles("ADMIN")), session: AsyncSession = Depends(get_db)) -> dict:
    user = await session.scalar(select(User).where(User.id == user_id, User.role == "VIEWER"))
    if user is None:
        raise HTTPException(404, "Viewer account not found")
    return _viewer_account(user)


@router.patch("/users/viewers/{user_id}")
async def set_viewer_status(user_id: uuid.UUID, request: ViewerStatusRequest, actor: User = Depends(require_roles("ADMIN")), session: AsyncSession = Depends(get_db)) -> dict:
    user = await session.scalar(select(User).where(User.id == user_id, User.role == "VIEWER").with_for_update())
    if user is None:
        raise HTTPException(404, "Viewer account not found")
    if user.is_active != request.is_active:
        user.is_active = request.is_active
        session.add(AuditLog(actor_id=actor.id, action="VIEWER_ENABLED" if request.is_active else "VIEWER_DISABLED", entity_type="USER", entity_id=user.id, details={"role": "VIEWER", "is_active": request.is_active}, source="auth_api"))
        await session.commit()
        await session.refresh(user)
    return _viewer_account(user)


@router.post("/token")
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], session: AsyncSession = Depends(get_db)) -> dict:
    user = await session.scalar(select(User).where(User.username == form.username))
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password", headers={"WWW-Authenticate": "Bearer"})
    user.last_login = datetime.now(timezone.utc)
    await session.commit()
    return {"access_token": create_access_token(user), "token_type": "bearer", "expires_in": 60 * 30}


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role, "is_active": user.is_active}

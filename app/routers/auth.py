"""Auth endpoints: register, login, refresh, logout, and /users/me keys."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    encrypt_with_dek,
    generate_refresh_token,
    get_or_create_dek,
    hash_password,
    verify_password,
)
from app.db import get_db
from app.deps import (
    check_api_key,
    check_auth_rate,
    clear_auth_cookies,
    consume_refresh_token,
    get_optional_user,
    revoke_refresh_token,
    set_auth_cookies,
    store_refresh_token,
)
from app.models import User, UserApiKeys
from app.schemas import (
    LoginReq,
    RegisterReq,
    TokenResp,
    UserKeysIn,
    UserKeysOut,
    UserResp,
)

router = APIRouter()


class _RefreshReq(BaseModel):
    refresh_token: Optional[str] = None


@router.post("/auth/register", response_model=TokenResp)
def register(req: RegisterReq, request: Request, response: Response, db: Session = Depends(get_db)):
    check_auth_rate(request)
    email = req.email.lower().strip()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    if len(req.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    user = User(email=email, hashed_password=hash_password(req.password))
    db.add(user)
    db.commit()
    access_token = create_access_token(user.id)
    refresh_token = generate_refresh_token()
    store_refresh_token(refresh_token, user.id)
    set_auth_cookies(response, access_token, refresh_token)
    return TokenResp(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        email=user.email,
    )


@router.post("/auth/login", response_model=TokenResp)
def login(req: LoginReq, request: Request, response: Response, db: Session = Depends(get_db)):
    check_auth_rate(request)
    email = req.email.lower().strip()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    access_token = create_access_token(user.id)
    refresh_token = generate_refresh_token()
    store_refresh_token(refresh_token, user.id)
    set_auth_cookies(response, access_token, refresh_token)
    return TokenResp(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        email=user.email,
    )


@router.post("/auth/refresh", response_model=TokenResp)
def refresh_access_token(
    req: _RefreshReq,
    response: Response,
    refresh_token_cookie: str = Cookie(default="", alias="refresh_token"),
    db: Session = Depends(get_db),
):
    """Rotate a refresh token and issue a new short-lived access token."""
    token = refresh_token_cookie or req.refresh_token or ""
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token provided")
    user_id = consume_refresh_token(token)
    if not user_id:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user = db.get(User, user_id)
    if not user:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="User not found")
    new_access = create_access_token(user.id)
    new_refresh = generate_refresh_token()
    store_refresh_token(new_refresh, user.id)
    set_auth_cookies(response, new_access, new_refresh)
    return TokenResp(
        access_token=new_access,
        refresh_token=new_refresh,
        user_id=user.id,
        email=user.email,
    )


@router.post("/auth/logout")
def logout(
    req: _RefreshReq,
    response: Response,
    refresh_token_cookie: str = Cookie(default="", alias="refresh_token"),
):
    """Revoke the refresh token and clear auth cookies."""
    token = refresh_token_cookie or req.refresh_token or ""
    if token:
        revoke_refresh_token(token)
    clear_auth_cookies(response)
    return {"logged_out": True}


@router.get("/users/me", response_model=UserResp, dependencies=[Depends(check_api_key)])
def get_me(current_user: Optional[User] = Depends(get_optional_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="JWT required for this endpoint")
    return UserResp(
        id=current_user.id,
        email=current_user.email,
        created_at=current_user.created_at,
        has_keys=current_user.api_keys is not None,
    )


@router.put("/users/me/keys", response_model=UserKeysOut, dependencies=[Depends(check_api_key)])
def upsert_user_keys(
    req: UserKeysIn,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="JWT required for this endpoint")
    keys = db.get(UserApiKeys, current_user.id)
    if not keys:
        keys = UserApiKeys(user_id=current_user.id, updated_at=datetime.utcnow())
        db.add(keys)
    dek = get_or_create_dek(keys)
    if req.openai_key is not None:
        keys.openai_key_enc = encrypt_with_dek(req.openai_key, dek) if req.openai_key else None
    if req.elevenlabs_key is not None:
        keys.elevenlabs_key_enc = encrypt_with_dek(req.elevenlabs_key, dek) if req.elevenlabs_key else None
    if req.elevenlabs_voice_id is not None:
        keys.elevenlabs_voice_id = req.elevenlabs_voice_id or None
    if req.elevenlabs_model_id is not None:
        keys.elevenlabs_model_id = req.elevenlabs_model_id or None
    keys.updated_at = datetime.utcnow()
    db.commit()
    return UserKeysOut(
        has_openai_key=bool(keys.openai_key_enc),
        has_elevenlabs_key=bool(keys.elevenlabs_key_enc),
        elevenlabs_voice_id=keys.elevenlabs_voice_id,
        elevenlabs_model_id=keys.elevenlabs_model_id,
    )


@router.get("/users/me/keys", response_model=UserKeysOut, dependencies=[Depends(check_api_key)])
def get_user_keys(
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="JWT required for this endpoint")
    keys = db.get(UserApiKeys, current_user.id)
    if not keys:
        return UserKeysOut(has_openai_key=False, has_elevenlabs_key=False)
    return UserKeysOut(
        has_openai_key=bool(keys.openai_key_enc),
        has_elevenlabs_key=bool(keys.elevenlabs_key_enc),
        elevenlabs_voice_id=keys.elevenlabs_voice_id,
        elevenlabs_model_id=keys.elevenlabs_model_id,
    )

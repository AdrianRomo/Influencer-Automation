"""Workspace routes — the multi-tenant root of the catalog-to-ad product."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import billing
from app.db import get_db
from app.deps import (
    assert_workspace_member,
    get_current_user,
    get_or_create_personal_workspace,
)
from app.models import User, Workspace, WorkspaceMember

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class GrantReq(BaseModel):
    amount: int = Field(..., gt=0, le=100000)
    reason: str = "grant"


def _serialize(ws: Workspace) -> dict:
    return {
        "id": ws.id,
        "name": ws.name,
        "owner_user_id": ws.owner_user_id,
        "plan": ws.plan,
        "credits_balance": ws.credits_balance,
        "created_at": ws.created_at.isoformat() if ws.created_at else None,
    }


@router.get("")
def list_workspaces(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """All workspaces the user owns or belongs to. Auto-creates a personal one."""
    get_or_create_personal_workspace(db, user)  # ensure at least one exists
    owned = db.execute(
        select(Workspace).where(Workspace.owner_user_id == user.id)
    ).scalars().all()
    member_ws_ids = db.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    ).scalars().all()
    extra = db.execute(
        select(Workspace).where(
            Workspace.id.in_(member_ws_ids),
            Workspace.owner_user_id != user.id,
        )
    ).scalars().all()
    return {"workspaces": [_serialize(w) for w in [*owned, *extra]]}


@router.post("", status_code=201)
def create_workspace(
    body: WorkspaceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ws = Workspace(name=body.name, owner_user_id=user.id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    db.commit()
    billing.grant(db, ws.id, billing.FREE_SIGNUP_CREDITS, reason="signup_grant",
                  idempotency_key=f"signup:{ws.id}")
    db.refresh(ws)
    return _serialize(ws)


@router.get("/{workspace_id}")
def get_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ws = assert_workspace_member(db, workspace_id, user)
    return _serialize(ws)


@router.get("/{workspace_id}/credits")
def get_credits(
    workspace_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Current balance + recent ledger, plus the per-operation price list."""
    assert_workspace_member(db, workspace_id, user)
    txns = billing.list_transactions(db, workspace_id, limit=50)
    return {
        "balance": billing.get_balance(db, workspace_id),
        "costs": {op: billing.cost(op) for op in ("analyze", "concept", "video")},
        "transactions": [
            {
                "id": t.id, "amount": t.amount, "reason": t.reason,
                "ref_type": t.ref_type, "ref_id": t.ref_id,
                "balance_after": t.balance_after,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in txns
        ],
    }


@router.post("/{workspace_id}/credits/grant")
def grant_credits(
    workspace_id: str,
    body: GrantReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    x_admin_key: str = Header(default=""),
):
    """Add credits (purchase / pilot top-up). Admin-gated via X-Admin-Key.

    A real billing flow (Stripe webhook) would call billing.grant() on payment;
    this manual endpoint covers pilots + comped accounts until that lands.
    """
    assert_workspace_member(db, workspace_id, user)
    if not ADMIN_API_KEY or x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Admin key required to grant credits")
    txn = billing.grant(db, workspace_id, body.amount, reason=body.reason or "grant")
    return {"workspace_id": workspace_id, "granted": body.amount, "balance": txn.balance_after}

"""Credit billing for the catalog-to-ad product.

Workspaces hold a ``credits_balance``. Expensive operations (analyze, concept
generation, video render) are charged at enqueue time and refunded if the task
fails. Every movement is recorded in the ``credit_transactions`` ledger; charges
and refunds are idempotent via ``idempotency_key`` so retries are safe.

Pricing is deterministic per operation (predictable for customers), overridable
via ``CREDIT_COST_<OP>`` env vars. New workspaces get ``FREE_SIGNUP_CREDITS``.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import CreditTransaction, Workspace

logger = logging.getLogger(__name__)

# Default credit cost per operation. analyze is cheap text; video is the pricey
# op (image gen + TTS + encode) and is priced to cover provider cost + margin.
_DEFAULT_COSTS = {"analyze": 1, "concept": 1, "video": 10}

FREE_SIGNUP_CREDITS = int(os.getenv("FREE_SIGNUP_CREDITS", "50"))


class InsufficientCredits(Exception):
    def __init__(self, needed: int, available: int) -> None:
        self.needed = needed
        self.available = available
        super().__init__(f"Insufficient credits: need {needed}, have {available}")


def cost(operation: str) -> int:
    """Credit price of an operation. Env override: CREDIT_COST_VIDEO=20 etc."""
    env = os.getenv(f"CREDIT_COST_{operation.upper()}")
    if env is not None:
        try:
            return max(0, int(env))
        except ValueError:
            pass
    return _DEFAULT_COSTS.get(operation, 1)


def get_balance(db: Session, workspace_id: str) -> int:
    bal = db.execute(
        select(Workspace.credits_balance).where(Workspace.id == workspace_id)
    ).scalar_one_or_none()
    return int(bal or 0)


def _ledger(
    db: Session, workspace_id: str, signed_amount: int, reason: str,
    ref_type: str | None, ref_id: str | None, idempotency_key: str | None,
) -> CreditTransaction:
    balance_after = get_balance(db, workspace_id)
    txn = CreditTransaction(
        workspace_id=workspace_id,
        amount=signed_amount,
        reason=reason,
        ref_type=ref_type,
        ref_id=ref_id,
        balance_after=balance_after,
        idempotency_key=idempotency_key,
    )
    db.add(txn)
    db.commit()
    return txn


def _existing(db: Session, idempotency_key: str | None) -> CreditTransaction | None:
    if not idempotency_key:
        return None
    return db.execute(
        select(CreditTransaction).where(CreditTransaction.idempotency_key == idempotency_key)
    ).scalar_one_or_none()


def charge(
    db: Session, workspace_id: str, amount: int, reason: str,
    *, ref_type: str | None = None, ref_id: str | None = None,
    idempotency_key: str | None = None,
) -> CreditTransaction:
    """Atomically debit credits. Raises InsufficientCredits if the balance is
    too low. Idempotent: a repeat with the same key returns the first txn."""
    if amount <= 0:
        # Free operation — record a zero-cost ledger entry for traceability.
        return _ledger(db, workspace_id, 0, reason, ref_type, ref_id, idempotency_key)

    dup = _existing(db, idempotency_key)
    if dup is not None:
        return dup

    # Conditional UPDATE: only succeeds when the balance can cover the charge.
    result = db.execute(
        update(Workspace)
        .where(Workspace.id == workspace_id, Workspace.credits_balance >= amount)
        .values(credits_balance=Workspace.credits_balance - amount)
    )
    if result.rowcount != 1:
        db.rollback()
        raise InsufficientCredits(amount, get_balance(db, workspace_id))

    return _ledger(db, workspace_id, -amount, reason, ref_type, ref_id, idempotency_key)


def grant(
    db: Session, workspace_id: str, amount: int, reason: str = "grant",
    *, ref_type: str | None = None, ref_id: str | None = None,
    idempotency_key: str | None = None,
) -> CreditTransaction:
    """Add credits (signup bonus, purchase, manual top-up). Idempotent."""
    if amount <= 0:
        return _ledger(db, workspace_id, 0, reason, ref_type, ref_id, idempotency_key)
    dup = _existing(db, idempotency_key)
    if dup is not None:
        return dup
    db.execute(
        update(Workspace).where(Workspace.id == workspace_id)
        .values(credits_balance=Workspace.credits_balance + amount)
    )
    return _ledger(db, workspace_id, amount, reason, ref_type, ref_id, idempotency_key)


def refund(
    db: Session, workspace_id: str, amount: int,
    *, ref_type: str | None = None, ref_id: str | None = None,
    idempotency_key: str | None = None,
) -> CreditTransaction | None:
    """Return credits after a failed operation. Idempotent — a duplicate key is
    a no-op (returns the existing refund) so a retried task can't double-refund."""
    if amount <= 0:
        return None
    dup = _existing(db, idempotency_key)
    if dup is not None:
        return dup
    db.execute(
        update(Workspace).where(Workspace.id == workspace_id)
        .values(credits_balance=Workspace.credits_balance + amount)
    )
    return _ledger(db, workspace_id, amount, "refund", ref_type, ref_id, idempotency_key)


def list_transactions(db: Session, workspace_id: str, limit: int = 50) -> list[CreditTransaction]:
    return list(db.execute(
        select(CreditTransaction)
        .where(CreditTransaction.workspace_id == workspace_id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(limit)
    ).scalars().all())

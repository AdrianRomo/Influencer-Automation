"""Stripe billing routes — buy credit packs via Checkout, fulfil via webhook.

Flow:
  1. Frontend calls POST /workspaces/{id}/billing/checkout {pack} → Stripe
     Checkout Session URL → user pays on Stripe.
  2. Stripe calls POST /billing/webhook on success. We verify the signature,
     then billing.grant() the pack's credits (idempotent on the Stripe event id,
     so duplicate webhook deliveries never double-credit).

Credits are granted ONLY by the webhook — never trust the client success_url.
If Stripe is not configured (no STRIPE_SECRET_KEY), the endpoints return 503 so
the rest of the app keeps working without a billing provider.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import billing
from app.db import get_db
from app.deps import assert_workspace_member, get_current_user
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["billing"])

_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL") or os.getenv("APP_URL", "http://localhost:5173")
_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL") or os.getenv("APP_URL", "http://localhost:5173")


class CheckoutReq(BaseModel):
    pack: str


@router.get("/billing/packs")
def list_packs():
    """Available credit packs + whether purchasing is configured."""
    return {
        "configured": billing.stripe_configured(),
        "packs": [
            {"id": pid, "credits": p["credits"], "label": p["label"],
             "price_configured": bool(billing.stripe_price_id(pid))}
            for pid, p in billing.CREDIT_PACKS.items()
        ],
    }


@router.post("/workspaces/{workspace_id}/billing/checkout")
def create_checkout(
    workspace_id: str,
    body: CheckoutReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a Stripe Checkout Session for a credit pack. Returns the hosted URL."""
    assert_workspace_member(db, workspace_id, user)

    pack = billing.CREDIT_PACKS.get(body.pack)
    if not pack:
        raise HTTPException(status_code=404, detail=f"Unknown pack: {body.pack}")
    if not billing.stripe_configured():
        raise HTTPException(status_code=503, detail="Billing is not configured")
    price_id = billing.stripe_price_id(body.pack)
    if not price_id:
        raise HTTPException(status_code=503, detail=f"No Stripe price configured for pack {body.pack}")

    import stripe
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{_SUCCESS_URL}?credits=ok",
            cancel_url=f"{_CANCEL_URL}?credits=cancel",
            client_reference_id=workspace_id,
            metadata={
                "workspace_id": workspace_id,
                "credits": str(pack["credits"]),
                "pack": body.pack,
                "user_id": user.id,
            },
        )
    except Exception as exc:
        logger.exception("Stripe checkout creation failed")
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}")

    return {"url": session.url, "session_id": session.id}


@router.post("/billing/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Fulfil paid Checkout Sessions. Verifies the Stripe signature, then grants
    credits idempotently on the event id. Credits are granted ONLY here."""
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not billing.stripe_configured() or not webhook_secret:
        raise HTTPException(status_code=503, detail="Billing webhook not configured")

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    import stripe
    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception as exc:
        # Bad signature / malformed payload → 400 so Stripe records the failure.
        logger.warning("Stripe webhook verification failed: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        md = sess.get("metadata") or {}
        workspace_id = md.get("workspace_id")
        try:
            credits = int(md.get("credits", 0))
        except (TypeError, ValueError):
            credits = 0
        # Only fulfil paid sessions.
        paid = sess.get("payment_status") in (None, "paid", "no_payment_required")
        if workspace_id and credits > 0 and paid:
            billing.grant(
                db, workspace_id, credits, reason="stripe_purchase",
                ref_type="stripe", ref_id=sess.get("id"),
                idempotency_key=f"stripe:{event['id']}",
            )
            logger.info("Granted %d credits to workspace %s (stripe event %s)",
                        credits, workspace_id, event["id"])

    return {"received": True}

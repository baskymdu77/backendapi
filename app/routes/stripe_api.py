import os
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
import stripe
from app.services.firebase_admin import get_firestore
from firebase_admin import firestore as firebase_firestore

router = APIRouter(prefix="/stripe", tags=["stripe"])


def get_stripe_api_key() -> str:
    api_key = os.getenv("STRIPE_SECRET_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY not configured")
    return api_key


def get_webhook_secret() -> str:
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET not configured")
    return secret


@router.get("/products")
async def list_products(
    limit: int = Query(10, ge=1, le=100),
    active: Optional[bool] = Query(None, description="Filter by active status"),
    include_prices: bool = Query(False, description="If true, attach all prices for each product"),
):
    """List Stripe products using the STRIPE_SECRET_KEY from environment variables."""
    try:
        stripe.api_key = get_stripe_api_key()
        # Expand default_price so each product includes its default price object
        params = {"limit": limit, "expand": ["data.default_price"]}
        if active is not None:
            params["active"] = active
        products = stripe.Product.list(**params)

        products_dict = products.to_dict()

        # Optionally attach all prices for each product
        if include_prices:
            for p in products.data:
                price_params = {"product": p.id, "limit": 100}
                if active is not None:
                    price_params["active"] = active
                price_list = stripe.Price.list(**price_params)

                # Attach prices to the matching product dict by id
                for d in products_dict.get("data", []):
                    if d.get("id") == p.id:
                        d["prices"] = price_list.to_dict().get("data", [])
                        break

        # Return as a plain dict so FastAPI can serialize it
        return products_dict
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CapturePaymentIntentRequest(BaseModel):
    projectId: str
    paymentIntentId: Optional[str] = None
    amountToCapture: Optional[int] = Field(
        None, ge=1, description="Optional partial capture amount in smallest currency unit"
    )


@router.post("/capture")
async def capture_payment_intent(payload: CapturePaymentIntentRequest):
    """Capture a PaymentIntent by projectId or explicit paymentIntentId.

    Strategy:
    - If paymentIntentId provided: capture it.
    - Else: read latest stripe event for the project from Firestore and capture its paymentIntentId if present.
    """
    try:
        stripe.api_key = get_stripe_api_key()

        pi_id = payload.paymentIntentId
        if not pi_id:
            # Try from Firestore latest event for the project
            db = get_firestore()
            proj_ref = db.collection("projects").document(payload.projectId)
            # Order by createdAt desc; take first
            docs = (
                proj_ref.collection("stripe")
                .order_by("createdAt", direction=firebase_firestore.Query.DESCENDING)
                .limit(1)
                .stream()
            )
            latest = None
            for d in docs:
                latest = d
                break
            if latest:
                data = latest.to_dict() or {}
                pi_id = data.get("paymentIntentId") or data.get("pi")

        if not pi_id:
            raise HTTPException(status_code=400, detail="paymentIntentId not found for project")

        # Capture (full or partial)
        if payload.amountToCapture:
            intent = stripe.PaymentIntent.capture(pi_id, amount_to_capture=payload.amountToCapture)
        else:
            intent = stripe.PaymentIntent.capture(pi_id)

        # Reflect in Firestore: set paid true when captured succeeded
        db = get_firestore()
        proj_ref = db.collection("projects").document(payload.projectId)
        proj_ref.set({"paid": True, "updatedAt": firebase_firestore.SERVER_TIMESTAMP}, merge=True)

        # Save capture result under subcollection
        proj_ref.collection("stripe").document(pi_id).set(
            {
                "eventType": "manual_capture",
                "paymentIntentId": pi_id,
                "status": intent.status,
                "amountCaptured": getattr(intent, "amount_received", None) or getattr(intent, "amount", None),
                "raw": intent.to_dict() if hasattr(intent, "to_dict") else dict(intent),
                "createdAt": firebase_firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

        return {"paymentIntentId": pi_id, "status": intent.status}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe webhook to handle payment events and update Firestore.

    - Verifies the event with STRIPE_WEBHOOK_SECRET
    - Reads projectId from PaymentIntent metadata
    - Stores an entry under projects/{projectId}/stripe/{paymentIntentId}
    - Updates projects/{projectId}.paid based on status
    """
    payload_bytes = await request.body()
    sig_header = request.headers.get("stripe-signature")
    endpoint_secret = get_webhook_secret()

    try:
        event = stripe.Webhook.construct_event(
            payload_bytes.decode("utf-8"), sig_header, endpoint_secret
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")

    event_type = event.get("type")
    data_object = event.get("data", {}).get("object", {})

    # Determine projectId and paymentIntentId
    project_id = None
    payment_intent_id = None

    if data_object.get("object") == "payment_intent":
        payment_intent_id = data_object.get("id")
        project_id = (data_object.get("metadata") or {}).get("projectId")
    elif data_object.get("object") == "charge":
        payment_intent_id = data_object.get("payment_intent")
        # Charge may carry metadata too
        project_id = (data_object.get("metadata") or {}).get("projectId")

    if not project_id:
        # Nothing to persist without project id
        return {"received": True, "ignored": True}

    db = get_firestore()
    proj_ref = db.collection("projects").document(project_id)
    stripe_ref = proj_ref.collection("stripe").document(payment_intent_id or event.get("id"))

    # Build a compact record
    record = {
        "eventId": event.get("id"),
        "eventType": event_type,
        "projectId": project_id,
        "paymentIntentId": payment_intent_id,
        "amount": data_object.get("amount"),
        "currency": data_object.get("currency"),
        "status": data_object.get("status"),
        "raw": data_object,
        "createdAt": firebase_firestore.SERVER_TIMESTAMP,
    }

    # Persist in subcollection
    stripe_ref.set(record, merge=True)

    # Update paid flag based on event
    paid = None
    if event_type in ("payment_intent.succeeded", "charge.succeeded"):
        paid = True
    elif event_type in ("payment_intent.payment_failed", "payment_intent.canceled", "charge.refunded"):
        paid = False

    if paid is not None:
        proj_ref.set({"paid": paid, "updatedAt": firebase_firestore.SERVER_TIMESTAMP}, merge=True)

    return {"received": True}


# ===== Payment Intent =====

class CheckoutItem(BaseModel):
    productId: str = Field(..., description="Stripe product id or your internal id")
    name: str
    unitAmount: int = Field(..., ge=0, description="Amount in smallest currency unit (e.g., cents)")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO currency code, e.g., 'usd'")
    quantity: int = Field(1, ge=1)


class CreatePaymentIntentRequest(BaseModel):
    projectId: str
    items: List[CheckoutItem]
    captureMethod: Optional[str] = Field(
        None,
        description="Stripe capture method: 'automatic' (default) or 'manual'",
    )


class PaymentIntentResponse(BaseModel):
    clientSecret: str
    paymentIntentId: str
    amount: int
    currency: str
    status: str


@router.post("/create-payment-intent", response_model=PaymentIntentResponse)
async def create_payment_intent(payload: CreatePaymentIntentRequest):
    """Create a Stripe PaymentIntent for the provided items."""
    try:
        stripe.api_key = get_stripe_api_key()

        if not payload.items:
            raise HTTPException(status_code=400, detail="No items provided")

        # Ensure single currency and valid amounts
        currency = payload.items[0].currency.lower()
        total = 0
        names = []
        for it in payload.items:
            if it.currency.lower() != currency:
                raise HTTPException(status_code=400, detail="All items must share the same currency")
            if it.unitAmount < 0 or it.quantity < 1:
                raise HTTPException(status_code=400, detail="Invalid unitAmount or quantity")
            total += it.unitAmount * it.quantity
            names.append(f"{it.name} x{it.quantity}")

        # Create PaymentIntent
        intent = stripe.PaymentIntent.create(
            amount=total,
            currency=currency,
            description=", ".join(names)[:500],
            automatic_payment_methods={"enabled": True},
            # If provided and valid, set capture_method (manual vs automatic)
            **({"capture_method": payload.captureMethod} if payload.captureMethod in {"manual", "automatic"} else {}),
            metadata={
                "projectId": payload.projectId,
            },
        )

        return PaymentIntentResponse(
            clientSecret=intent.client_secret,
            paymentIntentId=intent.id,
            amount=intent.amount,
            currency=intent.currency,
            status=intent.status,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===== Checkout Session =====

class CreateCheckoutSessionRequest(BaseModel):
    projectId: str
    successUrl: str
    cancelUrl: str
    items: List[CheckoutItem]
    captureMethod: Optional[str] = Field(
        None, description="Stripe capture method for Checkout PI: 'automatic' (default) or 'manual'"
    )


class CheckoutSessionResponse(BaseModel):
    id: str
    url: str


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(payload: CreateCheckoutSessionRequest):
    """Create Stripe Checkout Session and return hosted checkout URL."""
    try:
        stripe.api_key = get_stripe_api_key()

        if not payload.items:
            raise HTTPException(status_code=400, detail="No items provided")

        currency = payload.items[0].currency.lower()
        line_items = []
        for it in payload.items:
            if it.currency.lower() != currency:
                raise HTTPException(status_code=400, detail="All items must share the same currency")
            if it.unitAmount < 0 or it.quantity < 1:
                raise HTTPException(status_code=400, detail="Invalid unitAmount or quantity")
            line_items.append(
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {
                            "name": it.name,
                            "metadata": {"productId": it.productId},
                        },
                        "unit_amount": it.unitAmount,
                    },
                    "quantity": it.quantity,
                }
            )

        session = stripe.checkout.Session.create(
            mode="payment",
            success_url=f"{payload.successUrl}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=payload.cancelUrl,
            line_items=line_items,
            metadata={"projectId": payload.projectId},
            payment_intent_data={
                "metadata": {"projectId": payload.projectId},
                **({"capture_method": payload.captureMethod} if payload.captureMethod in {"manual", "automatic"} else {}),
            },
        )

        return CheckoutSessionResponse(id=session.id, url=session.url)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

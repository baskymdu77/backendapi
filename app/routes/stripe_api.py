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


def get_webhook_secrets() -> List[str]:
    """Return one or more webhook signing secrets.

    Supports either STRIPE_WEBHOOK_SECRET (single) or STRIPE_WEBHOOK_SECRETS (comma-separated).
    """
    multi = os.getenv("STRIPE_WEBHOOK_SECRETS")
    single = os.getenv("STRIPE_WEBHOOK_SECRET")
    secrets: List[str] = []
    if multi:
        secrets.extend([s.strip() for s in multi.split(",") if s.strip()])
    if single:
        secrets.append(single.strip())
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for s in secrets:
        if s and s not in seen:
            seen.add(s)
            deduped.append(s)
    if not deduped:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET(S) not configured")
    return deduped


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
    # Headers is case-insensitive; prefer canonical 'stripe-signature', but also check fallback
    sig_header = request.headers.get("stripe-signature") or request.headers.get("STRIPE_SIGNATURE")
    secrets = get_webhook_secrets()
    print("sig_header", sig_header)
    print("secrets", secrets)
    if not sig_header:
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing Stripe signature header. Expected 'stripe-signature'. "
                "Ensure your Stripe webhook is pointing to /api/v1/stripe/webhook and that any proxy/CDN forwards all headers."
            ),
        )

    last_err = None
    for secret in secrets:
        try:
            # Use raw UTF-8 decoded string of the exact request body
            payload_str = payload_bytes.decode("utf-8")
            event = stripe.Webhook.construct_event(payload_str, sig_header, secret)
            break
        except stripe.error.SignatureVerificationError as e:
            print("event", "SignatureVerificationError error")
            last_err = e
            event = None
            continue
        except Exception as e:
            print("event", "Exception event error")
            last_err = e
            event = None
            continue 
    if event is None:
        print("event", "non event error")
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid Stripe signature: {last_err}. Verify STRIPE_WEBHOOK_SECRET/STRIPE_WEBHOOK_SECRETS "
                f"match the source (CLI/Dashboard) and environment (test/live)."
            ),
        )

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
    elif data_object.get("object") == "checkout.session":
        # Persist Checkout session events for traceability, but do not toggle paid here
        payment_intent_id = data_object.get("payment_intent")
        project_id = (data_object.get("metadata") or {}).get("projectId")

    db = get_firestore()
    event_id = event.get("id")

    # Build a compact record
    record = {
        "eventId": event_id,
        "eventType": event_type,
        "objectType": data_object.get("object"),
        "projectId": project_id,
        "paymentIntentId": payment_intent_id,
        "amount": data_object.get("amount"),
        "currency": data_object.get("currency"),
        "status": data_object.get("status"),
        "raw": data_object,
        "createdAt": firebase_firestore.SERVER_TIMESTAMP,
    }

    # Always archive globally
    db.collection("stripe_events").document(event_id).set(record, merge=True)

    # If we have a project id, also persist under the project subcollection
    if project_id:
        proj_ref = db.collection("projects").document(project_id)
        proj_ref.collection("stripe").document(event_id).set(record, merge=True)

        # If this is a Checkout completion event, fetch line items and store them in the project doc
        if event_type == "checkout.session.completed":
            try:
                stripe.api_key = get_stripe_api_key()
                session_id = data_object.get("id")
                line_items = stripe.checkout.Session.list_line_items(
                    session_id, expand=["data.price.product"]
                )
                purchased_items = []
                for li in getattr(line_items, "data", []) or []:
                    price = li.get("price") or {}
                    product = price.get("product")
                    if isinstance(product, dict):
                        product_id = product.get("id")
                        product_name = product.get("name")
                        product_meta = product.get("metadata") or {}
                    else:
                        product_id = product
                        product_name = None
                        product_meta = {}
                    purchased_items.append(
                        {
                            "priceId": price.get("id"),
                            "productId": (product_meta.get("productId") if isinstance(product_meta, dict) else None) or product_id,
                            "productName": product_name or li.get("description"),
                            "quantity": li.get("quantity"),
                            "amountSubtotal": li.get("amount_subtotal"),
                            "amountTotal": li.get("amount_total"),
                            "currency": li.get("currency") or data_object.get("currency"),
                        }
                    )

                amount_total = data_object.get("amount_total")
                if amount_total is None:
                    try:
                        amount_total = sum([(i.get("amountTotal") or 0) for i in purchased_items])
                    except Exception:
                        amount_total = None

                proj_ref.set(
                    {
                        "lastPurchase": {
                            "source": "checkout",
                            "sessionId": session_id,
                            "paymentIntentId": payment_intent_id,
                            "items": purchased_items,
                            "amountTotal": amount_total,
                            "currency": data_object.get("currency"),
                            "updatedAt": firebase_firestore.SERVER_TIMESTAMP,
                        }
                    },
                    merge=True,
                )
            except Exception as e:
                # Log the error under the project's stripe subcollection but do not fail the webhook
                proj_ref.collection("stripe").document(f"{event_id}_items_error").set(
                    {"error": str(e), "createdAt": firebase_firestore.SERVER_TIMESTAMP}, merge=True
                )

            # Also append to a running purchasedItems array and persist a purchases/{eventId} history doc
            try:
                # 1) Append to purchasedItems (concatenate across purchases)
                items_with_keys = [
                    {**it, "eventId": event_id, "sessionId": session_id} for it in purchased_items
                ]
                proj_ref.set(
                    {
                        "purchasedItems": firebase_firestore.ArrayUnion(items_with_keys),
                        "updatedAt": firebase_firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )

                # 2) Write a per-purchase history document
                proj_ref.collection("purchases").document(event_id).set(
                    {
                        "eventId": event_id,
                        "sessionId": session_id,
                        "paymentIntentId": payment_intent_id,
                        "items": purchased_items,
                        "amountTotal": amount_total,
                        "currency": data_object.get("currency"),
                        "createdAt": firebase_firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )

                # 3) Maintain an aggregate by productId with summed quantity and totals
                try:
                    snap = proj_ref.get()
                    existing = snap.to_dict() if snap and snap.exists else {}
                except Exception:
                    existing = {}
                existing_items = ((existing or {}).get("purchaseAggregate") or {}).get("items") or []
                by_id = {}
                for it in existing_items:
                    pid = it.get("productId")
                    if not pid:
                        continue
                    by_id[pid] = {
                        "productId": pid,
                        "productName": it.get("productName"),
                        "quantity": int(it.get("quantity") or 0),
                        "amountTotal": int(it.get("amountTotal") or 0),
                        "currency": it.get("currency") or data_object.get("currency"),
                    }
                for it in purchased_items:
                    pid = it.get("productId")
                    if not pid:
                        continue
                    cur = by_id.get(pid) or {
                        "productId": pid,
                        "productName": it.get("productName"),
                        "quantity": 0,
                        "amountTotal": 0,
                        "currency": it.get("currency") or data_object.get("currency"),
                    }
                    cur["quantity"] = int(cur.get("quantity") or 0) + int(it.get("quantity") or 0)
                    cur["amountTotal"] = int(cur.get("amountTotal") or 0) + int(it.get("amountTotal") or 0)
                    # Prefer latest name/currency
                    if it.get("productName"):
                        cur["productName"] = it.get("productName")
                    if it.get("currency"):
                        cur["currency"] = it.get("currency")
                    by_id[pid] = cur

                proj_ref.set(
                    {
                        "purchaseAggregate": {
                            "items": list(by_id.values()),
                            "updatedAt": firebase_firestore.SERVER_TIMESTAMP,
                        }
                    },
                    merge=True,
                )
            except Exception as e:
                proj_ref.collection("stripe").document(f"{event_id}_concat_error").set(
                    {"error": str(e), "createdAt": firebase_firestore.SERVER_TIMESTAMP}, merge=True
                )

        # Update paid flag based on event (avoid toggling on checkout.session events)
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

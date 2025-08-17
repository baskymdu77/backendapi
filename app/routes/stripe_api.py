import os
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import stripe

router = APIRouter(prefix="/stripe", tags=["stripe"])


def get_stripe_api_key() -> str:
    api_key = os.getenv("STRIPE_SECRET_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY not configured")
    return api_key


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

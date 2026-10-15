from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .features import CATALOG, DEMO_PROFILES, build_features
from .model import CheckoutRisk
from .providers import DemoProvider, ProviderError, RazorpayTestProvider
from .store import Store, unpack

ROOT = Path(__file__).resolve().parents[2]


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str = "headphones"
    quantity: int = Field(default=1, ge=1, le=5)
    scenario: str = "returning"
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    razorpay_order_id: str = Field(pattern=r"^order_[A-Za-z0-9]+$", max_length=80)
    razorpay_payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9]+$", max_length=80)
    razorpay_signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewInput(BaseModel):
    approve: bool
    note: str = Field(min_length=10, max_length=500)


def create_app(db_path=None, provider=None, risk=None, merchant_token=None):
    provider_name = os.getenv("PAYMENTSHIELD_PROVIDER", "demo")
    if provider is None:
        if provider_name == "demo":
            provider = DemoProvider()
        elif provider_name == "razorpay_test":
            provider = RazorpayTestProvider(os.getenv("RAZORPAY_KEY_ID", ""),
                os.getenv("RAZORPAY_KEY_SECRET", ""), os.getenv("RAZORPAY_WEBHOOK_SECRET", ""))
        else:
            raise ValueError("Unsupported provider. Use demo or razorpay_test.")
    demo = provider.name == "demo"
    store = Store(Path(db_path or os.getenv("PAYMENTSHIELD_DB", ROOT / "runtime/connector.sqlite3")))
    risk = risk or CheckoutRisk(ROOT / "artifacts/checkout")
    token = merchant_token if merchant_token is not None else os.getenv("PAYMENTSHIELD_MERCHANT_TOKEN", "")
    app = FastAPI(title="PaymentShield Connector", version="0.1.0")
    app.state.store, app.state.provider = store, provider

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next):
        if request.method in {"POST", "PUT", "DELETE", "PATCH"} and request.url.path != "/api/v1/webhooks/razorpay":
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.netloc != request.url.netloc or parsed.scheme != request.url.scheme:
                    return Response("Cross-origin writes are not allowed.", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def session(request: Request, response: Response):
        value = request.cookies.get("ps_session", "")
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            value = secrets.token_hex(32)
            response.set_cookie("ps_session", value, httponly=True, samesite="strict",
                                secure=request.url.scheme == "https", max_age=86400)
        return hashlib.sha256(value.encode()).hexdigest()

    def merchant(authorization: str):
        if not token or not secrets.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Merchant access token required.")

    def owned(db, order_id, owner):
        row = db.execute("SELECT * FROM orders WHERE id=? AND session=?", (order_id, owner)).fetchone()
        if row is None:
            raise HTTPException(404, "Order not found.")
        return unpack(row)

    def event(db, order_id, kind, detail):
        db.execute("INSERT INTO events(order_id,kind,created,detail) VALUES(?,?,?,?)",
                   (order_id, kind, time.time(), detail))

    def public(row):
        return {key: row[key] for key in ("id", "item", "quantity", "amount", "created", "expires", "state", "assessment")}

    @app.get("/api/v1/config")
    def config(request: Request, response: Response):
        session(request, response)
        return {"provider": provider.name, "demo": demo, "catalog": CATALOG,
                "model_available": risk.bundle is not None,
                "merchant_configured": bool(token), "scenarios": list(DEMO_PROFILES) if demo else [],
                "notice": "Demo scenarios use synthetic history." if demo else "Provider test mode. No real money. Anonymous customers require review."}

    @app.post("/api/v1/orders", status_code=201)
    def create_order(body: OrderInput, request: Request, response: Response):
        owner = session(request, response)
        if body.item not in CATALOG or (demo and body.scenario not in DEMO_PROFILES):
            raise HTTPException(422, "Unknown product or demo scenario.")
        fingerprint = hashlib.sha256(json.dumps([body.item, body.quantity, body.scenario if demo else None]).encode()).hexdigest()
        now = time.time()
        with store.transaction() as db:
            existing = db.execute("SELECT * FROM orders WHERE session=? AND idem=?", (owner, body.idempotency_key)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise HTTPException(409, "Idempotency key was already used for a different order.")
                response.status_code = 200
                return public(unpack(existing))
            recent = db.execute("SELECT COUNT(*) AS attempts, SUM(state='failed') AS failures FROM orders WHERE session=? AND created>?",
                                (owner, now - 3600)).fetchone()
            if recent["attempts"] >= 30:
                raise HTTPException(429, "Too many orders in this browser session. Try later.")
            amount = CATALOG[body.item]["amount_paise"] * body.quantity
            features = build_features(amount, body.quantity, DEMO_PROFILES[body.scenario] if demo else None,
                                      time.gmtime(now).tm_hour, recent["attempts"], recent["failures"] or 0)
            assessment = risk.assess(features)
            state = {"proceed": "ready", "review": "review", "stop": "blocked"}[assessment["decision"]]
            order_id = "ps_" + uuid.uuid4().hex
            db.execute("""INSERT INTO orders(id,session,idem,fingerprint,item,quantity,amount,created,expires,state,assessment,features)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (order_id, owner, body.idempotency_key, fingerprint, body.item, body.quantity, amount,
                        now, now + 900, state, json.dumps(assessment), json.dumps(features)))
            event(db, order_id, "risk_assessed", assessment["decision"])
            return public(owned(db, order_id, owner))

    @app.get("/api/v1/orders/{order_id}")
    def get_order(order_id: str, request: Request, response: Response):
        owner = session(request, response)
        with store.transaction() as db:
            row = owned(db, order_id, owner)
            result = public(row)
            result["events"] = [dict(item) for item in db.execute(
                "SELECT kind,created,detail FROM events WHERE order_id=? ORDER BY id", (order_id,))]
            return result

    @app.post("/api/v1/orders/{order_id}/checkout")
    def checkout(order_id: str, request: Request, response: Response):
        owner = session(request, response)
        error = None
        with store.transaction() as db:
            row = owned(db, order_id, owner)
            if row["state"] not in {"ready", "provider_pending"}:
                raise HTTPException(409, "This order is not cleared for checkout.")
            if row["expires"] < time.time():
                raise HTTPException(409, "Order expired. Start a new order.")
            if not row["provider_order"]:
                try:
                    external = provider.create_order(row["amount"], order_id)
                    db.execute("UPDATE orders SET provider_order=?,state='provider_pending' WHERE id=?", (external["id"], order_id))
                    event(db, order_id, "provider_order_created", provider.name)
                    row["provider_order"] = external["id"]
                except ProviderError:
                    # A timeout might mean the provider created an order. Never blindly create a second one.
                    db.execute("UPDATE orders SET state='provider_unknown' WHERE id=?", (order_id,))
                    event(db, order_id, "provider_unknown", "External order creation needs reconciliation.")
                    error = True
            result = {"provider": provider.name, "key": provider.key_id, "order_id": row["provider_order"],
                      "amount": row["amount"], "currency": "INR", "name": "PaymentShield Demo Store"}
        if error:
            raise HTTPException(502, "Provider result unknown. Do not retry this order; check with the merchant.")
        return result

    @app.post("/api/v1/orders/{order_id}/demo-complete")
    def demo_complete(order_id: str, request: Request, response: Response, outcome: str = "success"):
        if not demo:
            raise HTTPException(404, "Demo completion is disabled for external providers.")
        if outcome not in {"success", "failure"}:
            raise HTTPException(422, "Invalid outcome.")
        owner = session(request, response)
        with store.transaction() as db:
            row = owned(db, order_id, owner)
            if row["state"] in {"paid", "failed"}:
                return public(row)
            if row["state"] != "provider_pending":
                raise HTTPException(409, "Order is not awaiting a simulated payment.")
            state = "paid" if outcome == "success" else "failed"
            db.execute("UPDATE orders SET state=?,payment_id=? WHERE id=?", (state, "demo_pay_" + uuid.uuid4().hex, order_id))
            event(db, order_id, "demo_" + state, "Simulated outcome. No money moved.")
            return public(owned(db, order_id, owner))

    @app.post("/api/v1/orders/{order_id}/verify")
    def verify(order_id: str, body: Verification, request: Request, response: Response):
        if demo:
            raise HTTPException(404, "External verification is disabled in demo mode.")
        owner = session(request, response)
        with store.transaction() as db:
            row = owned(db, order_id, owner)
            if row["state"] not in {"provider_pending", "authorized", "paid", "failed"} or body.razorpay_order_id != row["provider_order"]:
                raise HTTPException(409, "Payment is not associated with this checkout.")
            try:
                payment = provider.verify_payment(row["provider_order"], body.razorpay_payment_id, body.razorpay_signature, row["amount"])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except ProviderError as exc:
                raise HTTPException(502, "Could not confirm the payment. Await provider confirmation.") from exc
            state = {"captured": "paid", "authorized": "authorized", "failed": "failed"}.get(payment.get("status"))
            if state and row["state"] != "paid":
                db.execute("UPDATE orders SET state=?,payment_id=? WHERE id=?", (state, body.razorpay_payment_id, order_id))
                event(db, order_id, "provider_verified", state)
            return public(owned(db, order_id, owner))

    @app.post("/api/v1/webhooks/razorpay")
    async def webhook(request: Request, x_razorpay_signature: str = Header(default="")):
        if demo:
            raise HTTPException(404, "Webhook disabled in demo mode.")
        raw = await request.body()
        if len(raw) > 256_000:
            raise HTTPException(413, "Webhook too large.")
        if not provider.validate_webhook(raw, x_razorpay_signature):
            raise HTTPException(400, "Invalid webhook signature.")
        try:
            payload = json.loads(raw)
            payment = payload["payload"]["payment"]["entity"]
            kind = payload["event"]
            if not isinstance(payment, dict):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "Malformed webhook.")
        supported = {"payment.captured": ("captured", "paid"), "payment.authorized": ("authorized", "authorized"), "payment.failed": ("failed", "failed")}
        if kind not in supported:
            return {"received": True, "ignored": True}
        digest = hashlib.sha256(raw).hexdigest()
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM webhook_events WHERE digest=?", (digest,)).fetchone():
                return {"received": True, "duplicate": True}
            row = db.execute("SELECT * FROM orders WHERE provider_order=?", (payment.get("order_id"),)).fetchone()
            expected_status, state = supported[kind]
            if row is None:
                # Retry later: webhook may race provider-order persistence.
                raise HTTPException(409, "Provider order not registered yet.")
            if (payment.get("amount") != row["amount"] or payment.get("currency") != "INR"
                    or payment.get("status") != expected_status or not re.fullmatch(r"pay_[A-Za-z0-9]+", str(payment.get("id", "")))):
                raise HTTPException(400, "Webhook payment details do not match.")
            if row["state"] not in {"provider_pending", "authorized", "failed", "paid"}:
                raise HTTPException(409, "Order is not in a provider-payment state.")
            if row["state"] != "paid" and not (row["state"] == "authorized" and state == "failed" and row["payment_id"] != payment["id"]):
                db.execute("UPDATE orders SET state=?,payment_id=? WHERE id=?", (state, payment["id"], row["id"]))
                event(db, row["id"], "webhook_verified", kind)
            db.execute("INSERT INTO webhook_events(digest) VALUES(?)", (digest,))
        return {"received": True}

    @app.get("/api/v1/merchant/orders")
    def merchant_orders(authorization: str = Header(default="")):
        merchant(authorization)
        with store.transaction() as db:
            return [public(unpack(row)) for row in db.execute("SELECT * FROM orders ORDER BY created DESC LIMIT 200")]

    @app.post("/api/v1/merchant/orders/{order_id}/review")
    def review(order_id: str, body: ReviewInput, authorization: str = Header(default="")):
        merchant(authorization)
        with store.transaction() as db:
            row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Order not found.")
            if row["state"] != "review" or row["expires"] < time.time():
                raise HTTPException(409, "Only unexpired review orders can be reviewed; blocked orders cannot be overridden.")
            state = "ready" if body.approve else "blocked"
            db.execute("UPDATE orders SET state=?,review_note=? WHERE id=?", (state, body.note, order_id))
            event(db, order_id, "merchant_review", "Approved after independent review." if body.approve else "Declined by merchant.")
            return {"id": order_id, "state": state}

    @app.get("/api/v1/evaluation")
    def evaluation():
        if risk.report is None:
            raise HTTPException(503, "Train the checkout model to generate the evaluation report.")
        return risk.report

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web/index.html")

    return app


app = create_app()

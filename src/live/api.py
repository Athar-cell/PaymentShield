from __future__ import annotations

import asyncio
import base64
from collections import defaultdict, deque
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import statistics
import threading
import time
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

try:
    from src.fraudshield.qr import decode_qr_image, parse_upi_payload  # type: ignore[assignment]
except Exception:  # cv2/PIL missing in serverless
    decode_qr_image = None  # type: ignore[assignment]
    parse_upi_payload = None  # type: ignore[assignment]
from .contracts import Authorize, Feedback, Intent, ManualCheck, Outcome, QRCheck, QRImageCheck, Review
from .engine import assess, VERSION
from .storage import LiveStore

ROOT = Path(__file__).resolve().parents[2]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: bytes):
    return hashlib.sha256(value).hexdigest()


def signing_message(method, path, timestamp, nonce, raw):
    return f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{digest(raw)}".encode()


def create_app(db_path=None, integration_id=None, integration_secret=None, console_token=None):
    key_id = integration_id if integration_id is not None else os.getenv("PAYMENTSHIELD_INTEGRATION_ID", "my-app")
    secret = integration_secret if integration_secret is not None else os.getenv("PAYMENTSHIELD_INTEGRATION_SECRET", "")
    console_key = console_token if console_token is not None else os.getenv("PAYMENTSHIELD_MERCHANT_TOKEN", "")
    configured = len(secret) >= 32
    store = LiveStore(db_path or os.getenv("PAYMENTSHIELD_LIVE_DB", str(ROOT / "runtime/live.sqlite3")))
    app = FastAPI(title="PaymentShield Live Risk API", version="1.0.0")
    app.state.store = store
    limits, limits_lock = defaultdict(deque), threading.Lock()

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.method == "POST" and request.headers.get("origin"):
            origin = urlsplit(request.headers["origin"])
            if origin.netloc != request.url.netloc or origin.scheme != request.url.scheme:
                return Response("Cross-origin browser writes are disabled.", status_code=403)
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            result.headers["Cache-Control"] = "no-store"
        return result

    def keyed(value):
        return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()

    async def limited_body(request, limit):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "Request exceeds the permitted size.")
            chunks.append(chunk)
        return b"".join(chunks)

    async def authenticate(request):
        if not configured:
            raise HTTPException(503, "Configure a server-side integration secret of at least 32 characters.")
        raw = await limited_body(request, 32_000)
        timestamp, nonce = request.headers.get("x-ps-timestamp", ""), request.headers.get("x-ps-nonce", "")
        if request.headers.get("x-ps-key-id") != key_id:
            raise HTTPException(401, "Unknown integration key ID.")
        if not re.fullmatch(r"[0-9]{1,12}", timestamp) or abs(time.time() - int(timestamp)) > 60:
            raise HTTPException(401, "Request timestamp expired or invalid.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", nonce):
            raise HTTPException(401, "Invalid request nonce.")
        expected = hmac.new(secret.encode(), signing_message(request.method, request.url.path, timestamp, nonce, raw), hashlib.sha256).hexdigest()
        supplied_signature = request.headers.get("x-ps-signature", "")
        if not re.fullmatch(r"[a-f0-9]{64}", supplied_signature) or not hmac.compare_digest(expected, supplied_signature):
            raise HTTPException(401, "Invalid request signature.")
        with store.transaction() as db:
            db.execute("DELETE FROM live_nonces WHERE created<?", (time.time() - 120,))
            if db.execute("SELECT 1 FROM live_nonces WHERE nonce=?", (nonce,)).fetchone():
                raise HTTPException(409, "Replay rejected. Retry with a fresh nonce and the same transaction ID.")
            db.execute("INSERT INTO live_nonces VALUES(?,?)", (nonce, time.time()))

    def manual_limit(request):
        # Basic process-local abuse protection, not a distributed production limiter.
        key = digest((request.client.host if request.client else "unknown").encode())
        now = time.monotonic()
        with limits_lock:
            queue = limits[key]
            while queue and queue[0] < now - 60:
                queue.popleft()
            if len(queue) >= 30:
                raise HTTPException(429, "Too many checks. Wait one minute.")
            queue.append(now)

    def touch(db):
        db.execute("UPDATE live_revision SET value=value+1 WHERE id=1")
        db.execute("INSERT INTO live_connection VALUES(1,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen", (time.time(),))

    def refs(intent):
        scope = intent["environment"] + ":"
        return {"customer": keyed(scope + "customer:" + intent["customer_ref"]),
                "device": keyed(scope + "device:" + intent["device_ref"]) if intent.get("device_ref") else None,
                "payee": keyed(scope + "payee:" + intent["payee"].casefold()),
                "transaction": keyed("transaction:" + intent["transaction_id"])}

    def history(db, keys, exclude=None):
        now = time.time()
        rows = db.execute("""SELECT a.*,l.label FROM live_assessments a LEFT JOIN live_labels l ON l.assessment_id=a.id
                             WHERE customer=? AND a.created>? AND (? IS NULL OR a.id!=?)
                             ORDER BY a.created DESC LIMIT 1000""", (keys["customer"], now - 90 * 86400, exclude, exclude)).fetchall()
        # Pending later outcomes are not historical success: use only the stored state at assessment time.
        paid = [row for row in rows if row["payment_status"] == "paid" and row["label"] != 1]
        return {"attempts_10m": sum(row["created"] > now - 600 for row in rows),
                "failed_1h": sum((row["outcome_at"] or 0) > now - 3600 and row["payment_status"] == "failed" for row in rows),
                "paid_count": len(paid), "recipient_seen": any(row["payee"] == keys["payee"] for row in paid),
                "device_known": keys["device"] is not None and any(row["device"] == keys["device"] for row in paid),
                "median_paid_amount": statistics.median([row["amount"] for row in paid]) if paid else None}

    def fingerprint(intent):
        return keyed("intent:" + canonical(intent))

    def permit(row):
        return hmac.new(secret.encode(), f"permit:{row['id']}:{row['fingerprint']}:{row['permit_expires']}".encode(), hashlib.sha256).hexdigest()

    def public(row, include_permit=False):
        result = json.loads(row["result"])
        result.pop("features", None)
        result.update({"assessment_id": row["id"], "received_at": row["created"],
                       "environment": row["environment"], "payment_status": row["payment_status"],
                       "permit_expires": row["permit_expires"], "consumed": bool(row["consumed"]),
                       "independent_review_reported": bool(row["reviewed"]),
                       "permit": permit(row) if include_permit and row["permit_hash"] and not row["consumed"] and row["permit_expires"] > time.time() else None})
        return result

    def console(request):
        value = request.cookies.get("ps_console", "")
        with store.transaction() as db:
            valid = db.execute("SELECT 1 FROM live_console_sessions WHERE token_hash=? AND expires>?", (digest(value.encode()), time.time())).fetchone()
        if not valid:
            raise HTTPException(401, "Connect the operator console with its local access token.")

    def state():
        with store.transaction() as db:
            counts = {row["environment"]: row["n"] for row in db.execute("SELECT environment,COUNT(*) n FROM live_assessments GROUP BY environment")}
            connection = db.execute("SELECT last_seen FROM live_connection WHERE id=1").fetchone()
            rows = db.execute("SELECT * FROM live_assessments ORDER BY created DESC LIMIT 30").fetchall()
            revision = db.execute("SELECT value FROM live_revision WHERE id=1").fetchone()[0]
            labels = db.execute("""SELECT COUNT(*) n,COALESCE(SUM(l.label),0) positive FROM live_labels l
                                   JOIN live_assessments a ON a.id=l.assessment_id WHERE a.environment='live'""").fetchone()
        return {"revision": revision, "counts": counts, "last_request_at": connection[0] if connection else None,
                "assessments": [public(row) for row in rows], "bank_feed_connected": False,
                "training_data": {"labelled_live_events": labels["n"], "positive_labels": labels["positive"],
                                  "provenance": "Integrator-adjudicated labels, not independently verified."},
                "notice": "Incoming app requests, not a bank-wide or Google Pay feed. Test and live namespaces are separate."}

    @app.get("/api/v2/status")
    def status():
        return {"engine_version": VERSION, "integration_configured": configured,
                "integration_id": key_id, "console_configured": bool(console_key),
                "bank_feed_connected": False, "payment_execution_enabled": False,
                "ml_enforcement": "disabled", "entry_points": ["signed_app_api", "manual_upi", "qr_image"],
                "storage": "Local SQLite; single integration, single-service prototype."}

    @app.post("/api/v2/assess")
    async def assess_intent(body: Intent, request: Request):
        await authenticate(request)
        start = time.perf_counter()
        value = body.model_dump()
        keys, fp = refs(value), fingerprint(value)
        with store.transaction() as db:
            existing = db.execute("SELECT * FROM live_assessments WHERE tx_ref=?", (keys["transaction"],)).fetchone()
            if existing:
                if not hmac.compare_digest(existing["fingerprint"], fp):
                    raise HTTPException(409, "Transaction ID already belongs to different payment details.")
                result = public(existing, True)
                result["idempotent"] = True
                return result
            context = history(db, keys)
            if context["attempts_10m"] >= 100:
                raise HTTPException(429, "Assessment limit reached for this customer.")
            result = assess(value, context)
            result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
            identifier, now = "risk_" + uuid.uuid4().hex, time.time()
            expires = now + 60 if result["decision"] == "proceed" else None
            token = permit({"id": identifier, "fingerprint": fp, "permit_expires": expires}) if expires else None
            db.execute("""INSERT INTO live_assessments(id,tx_ref,fingerprint,customer,device,payee,amount,created,source,environment,
                          result,permit_hash,permit_expires,features) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (identifier, keys["transaction"], fp, keys["customer"], keys["device"], keys["payee"], value["amount_paise"],
                        now, "signed_integration", value["environment"], canonical(result), digest(token.encode()) if token else None,
                        expires, canonical(result["features"])))
            touch(db)
            row = db.execute("SELECT * FROM live_assessments WHERE id=?", (identifier,)).fetchone()
            return public(row, True)

    @app.post("/api/v2/authorize")
    async def authorize(body: Authorize, request: Request):
        await authenticate(request)
        value = body.intent.model_dump()
        keys, fp = refs(value), fingerprint(value)
        rejected = None
        with store.transaction() as db:
            row = db.execute("SELECT * FROM live_assessments WHERE tx_ref=?", (keys["transaction"],)).fetchone()
            if not row or not hmac.compare_digest(row["fingerprint"], fp):
                raise HTTPException(409, "Payment details changed or were never assessed.")
            if not row["permit_hash"] or not hmac.compare_digest(row["permit_hash"], digest(body.permit.encode())):
                raise HTTPException(403, "No valid proceed permit for this payment.")
            if row["consumed"] or row["permit_expires"] < time.time():
                raise HTTPException(409, "Permit expired or already consumed. Never blindly repeat a payment.")
            fresh = assess(value, history(db, keys, exclude=row["id"]))
            earlier_codes = {signal["code"] for signal in json.loads(row["result"])["signals"]}
            new_signal = any(signal["code"] not in earlier_codes for signal in fresh["signals"])
            may_use_review = row["reviewed"] and fresh["decision"] != "stop" and not new_signal
            if fresh["decision"] != "proceed" and not may_use_review:
                db.execute("UPDATE live_assessments SET permit_hash=NULL,result=?,features=? WHERE id=?",
                           (canonical(fresh), canonical(fresh["features"]), row["id"]))
                touch(db)
                rejected = fresh
            else:
                db.execute("UPDATE live_assessments SET consumed=1,consumed_at=? WHERE id=?", (time.time(), row["id"]))
                touch(db)
        if rejected:
            raise HTTPException(409, "New evidence requires verification. This permit was revoked.")
        return {"permitted": True, "assessment_id": row["id"], "scope": "Permission for the integrating app to attempt this exact payment, not bank authorization or settlement."}

    @app.post("/api/v2/review")
    async def review(body: Review, request: Request):
        await authenticate(request)
        with store.transaction() as db:
            row = db.execute("SELECT * FROM live_assessments WHERE tx_ref=?", (keyed("transaction:" + body.transaction_id),)).fetchone()
            if row is None:
                raise HTTPException(404, "Unknown transaction.")
            result = json.loads(row["result"])
            if row["consumed"] or row["payment_status"] or result["decision"] != "verify":
                raise HTTPException(409, "Only an unconsumed verify decision can receive independent clearance; stop decisions cannot be overridden.")
            if time.time() - row["created"] > 900:
                raise HTTPException(409, "Assessment expired. Create a fresh transaction and check again.")
            expires = time.time() + 60
            token = permit({"id": row["id"], "fingerprint": row["fingerprint"], "permit_expires": expires})
            result["review"] = {"provenance": "integrator_reported_independent_verification", "evidence_sha256": digest(body.evidence.encode()), "at": time.time()}
            db.execute("UPDATE live_assessments SET reviewed=1,permit_hash=?,permit_expires=?,result=? WHERE id=?",
                       (digest(token.encode()), expires, canonical(result), row["id"]))
            touch(db)
            row = db.execute("SELECT * FROM live_assessments WHERE id=?", (row["id"],)).fetchone()
            return public(row, True)

    @app.post("/api/v2/outcomes")
    async def outcome(body: Outcome, request: Request):
        await authenticate(request)
        tx = keyed("transaction:" + body.transaction_id)
        event = keyed("event:" + body.event_id)
        fp = keyed(canonical(body.model_dump()))
        with store.transaction() as db:
            duplicate = db.execute("SELECT fingerprint FROM live_outcomes WHERE event_ref=?", (event,)).fetchone()
            if duplicate:
                if duplicate[0] != fp:
                    raise HTTPException(409, "Event ID was reused for a different outcome.")
                return {"recorded": True, "duplicate": True}
            row = db.execute("SELECT * FROM live_assessments WHERE tx_ref=?", (tx,)).fetchone()
            if not row:
                raise HTTPException(404, "Unknown transaction.")
            # Outcomes are authenticated claims by the integrator, not bank-verified records.
            if row["payment_status"] and row["payment_status"] != body.status:
                raise HTTPException(409, "Conflicting final outcome requires operator reconciliation.")
            provider_ref = keyed("provider:" + body.provider_reference)
            if row["outcome_ref"] and row["outcome_ref"] != provider_ref:
                raise HTTPException(409, "Provider reference changed for a final outcome. Reconcile independently.")
            db.execute("UPDATE live_assessments SET payment_status=?,outcome_ref=?,outcome_at=COALESCE(outcome_at,?) WHERE id=?",
                       (body.status, provider_ref, time.time(), row["id"]))
            db.execute("INSERT INTO live_outcomes VALUES(?,?)", (event, fp))
            touch(db)
        return {"recorded": True, "provenance": "integration_reported", "bank_verified": False}

    @app.post("/api/v2/feedback")
    async def feedback(body: Feedback, request: Request):
        await authenticate(request)
        with store.transaction() as db:
            row = db.execute("SELECT id FROM live_assessments WHERE tx_ref=?", (keyed("transaction:" + body.transaction_id),)).fetchone()
            if row is None:
                raise HTTPException(404, "Unknown transaction.")
            existing = db.execute("SELECT label FROM live_labels WHERE assessment_id=?", (row[0],)).fetchone()
            if existing is not None:
                raise HTTPException(409, "A label is already recorded. Resolve corrections with an audited process.")
            # Evidence text may contain PII, so retain its digest, not the raw narrative.
            db.execute("INSERT INTO live_labels VALUES(?,?,?,?)", (row[0], body.label, digest(body.evidence.encode()), time.time()))
            touch(db)
        return {"recorded": True, "provenance": "integrator_adjudicated_label", "independently_verified": False}

    @app.post("/api/v2/check")
    def check(body: ManualCheck, request: Request):
        manual_limit(request)
        return assess(body.model_dump(), {}, "user_supplied")

    def qr_result(parsed, amount, expected=None, message="", purpose="purchase"):
        if parsed["amount_inr"] is not None and (not math.isfinite(parsed["amount_inr"]) or not 0 < parsed["amount_inr"] <= 1_000_000):
            raise ValueError("QR amount exceeds the supported range.")
        encoded = round(parsed["amount_inr"] * 100) if parsed["amount_inr"] is not None else None
        value = {"amount_paise": encoded if encoded is not None else amount, "expected_amount_paise": amount,
                 "payee": parsed["payee_upi_id"], "expected_payee": expected, "message": message, "purpose": purpose}
        result = assess(value, {}, "user_supplied")
        result["qr"] = {"payee": parsed["payee_upi_id"], "self_declared_name": parsed["payee_name"],
                        "encoded_amount_paise": encoded, "name_verified": False}
        return result

    @app.post("/api/v2/check-qr")
    def check_qr(body: QRCheck, request: Request):
        manual_limit(request)
        if parse_upi_payload is None:
            raise HTTPException(503, "QR decoding unavailable in this deployment (cv2/PIL not installed).")
        parsed = urlsplit(body.payload)
        if parsed.path not in {"", "/"} or parsed.fragment:
            raise HTTPException(422, "Unexpected path or fragment in payment URI.")
        try:
            return qr_result(parse_upi_payload(body.payload), body.amount_paise, body.expected_payee, body.message, body.purpose)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/v2/check-qr-image")
    async def check_qr_image(request: Request):
        manual_limit(request)
        if decode_qr_image is None:
            raise HTTPException(503, "QR image decoding unavailable in this deployment (cv2/PIL not installed).")
        raw_json = await limited_body(request, 12 * 1024 * 1024)
        try:
            body = QRImageCheck.model_validate_json(raw_json)
            raw = base64.b64decode(body.image_base64, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid QR image or payment context.")
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(413, "QR image exceeds 8 MiB.")
        try:
            parsed = await asyncio.to_thread(decode_qr_image, raw)
            return qr_result(parsed, body.amount_paise, body.expected_payee, body.message, body.purpose)
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise HTTPException(422, str(exc)) from exc
            raise HTTPException(422, "Could not decode this image. Use a clear PNG, JPG or WebP QR image.") from exc

    @app.post("/api/v2/console/session")
    def login(request: Request, response: Response):
        supplied = request.headers.get("authorization", "")
        if not console_key or not secrets.compare_digest(supplied.encode(), ("Bearer " + console_key).encode()):
            raise HTTPException(401, "Invalid operator access token.")
        value = secrets.token_urlsafe(32)
        with store.transaction() as db:
            db.execute("DELETE FROM live_console_sessions WHERE expires<?", (time.time(),))
            db.execute("INSERT INTO live_console_sessions VALUES(?,?)", (digest(value.encode()), time.time() + 1800))
        response.set_cookie("ps_console", value, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=1800)
        return {"connected": True, "scope": "Read-only operator console"}

    @app.get("/api/v2/console/state")
    def console_state(request: Request):
        console(request)
        return state()

    @app.get("/api/v2/console/model")
    def model_report(request: Request):
        console(request)
        report = ROOT / "artifacts/observed/evaluation.json"
        if not report.is_file():
            return {"available": False, "enforcement": "disabled", "reason": "No observed-data model report exists. Export adjudicated live events and run scripts/train_observed.py."}
        try:
            value = json.loads(report.read_text(encoding="utf-8"))
            if value.get("source") != "observed_app_events_with_integrator_labels":
                raise ValueError("Wrong dataset provenance")
            return {"available": True, "enforcement": "disabled", "report": value}
        except (ValueError, OSError):
            raise HTTPException(503, "Observed model report is invalid; live ML enforcement remains disabled.")

    @app.get("/api/v2/console/stream")
    async def stream(request: Request):
        console(request)
        async def updates():
            last = -1
            while not await request.is_disconnected():
                try:
                    console(request)
                except HTTPException:
                    yield "event: expired\ndata: {}\n\n"
                    return
                snapshot = state()
                if snapshot["revision"] != last:
                    yield f"event: state\ndata: {canonical(snapshot)}\n\n"
                    last = snapshot["revision"]
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(updates(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/v2/training/export")
    async def training_export(request: Request):
        await authenticate(request)
        with store.transaction() as db:
            rows = db.execute("""SELECT a.id,a.customer,a.created,a.features,l.label FROM live_assessments a
                                 JOIN live_labels l ON a.id=l.assessment_id WHERE a.environment='live' ORDER BY a.created""").fetchall()
        return {"source": "observed_app_events_with_integrator_labels", "independently_verified": False,
                "rows": [{"assessment_id": row["id"], "customer_group": row["customer"], "timestamp": row["created"],
                          **json.loads(row["features"]), "label_fraud": row["label"]} for row in rows]}

    live_web_dir = ROOT / "live_web"
    if live_web_dir.is_dir():
        app.mount("/static", StaticFiles(directory=live_web_dir), name="static")

        @app.get("/")
        def home():
            return FileResponse(live_web_dir / "index.html")
    else:
        @app.get("/")
        def home():  # type: ignore[misc]
            return {
                "status": "ok",
                "service": "PaymentShield Live Risk Gateway",
                "version": VERSION,
                "docs": "/docs",
                "check": "/api/v2/check",
            }

    return app


app = create_app()

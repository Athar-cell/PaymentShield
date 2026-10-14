"""Server-side connector SDK. Never ship the signing secret to a browser."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlsplit

import httpx


class PaymentShieldClient:
    def __init__(self, base_url: str, key_id: str, secret: str, transport=None):
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1", "testserver"}):
            raise ValueError("Use HTTPS outside local development.")
        if len(secret) < 32:
            raise ValueError("Use a signing secret with at least 32 characters.")
        self.base = base_url.rstrip("/")
        self.key_id, self._secret = key_id, secret
        self.client = transport or httpx.Client(base_url=self.base, timeout=10)

    def call(self, path, payload=None):
        method = "GET" if payload is None else "POST"
        raw = b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{hashlib.sha256(raw).hexdigest()}".encode()
        signature = hmac.new(self._secret.encode(), message, hashlib.sha256).hexdigest()
        response = self.client.request(method, self.base + path, content=raw, headers={
            "Content-Type": "application/json", "X-PS-Key-Id": self.key_id,
            "X-PS-Timestamp": timestamp, "X-PS-Nonce": nonce, "X-PS-Signature": signature})
        response.raise_for_status()
        return response.json()

    def assess(self, intent):
        return self.call("/api/v2/assess", intent)

    def independently_verified(self, transaction_id, evidence):
        """Call only after YOUR app actually performed independent verification."""
        return self.call("/api/v2/review", {"transaction_id": transaction_id, "evidence": evidence})

    def authorize(self, intent, permit):
        return self.call("/api/v2/authorize", {"intent": intent, "permit": permit})

    def record_outcome(self, transaction_id, event_id, status, provider_reference):
        return self.call("/api/v2/outcomes", {"transaction_id": transaction_id, "event_id": event_id,
                         "status": status, "provider_reference": provider_reference})

    def record_label(self, transaction_id, label, evidence):
        return self.call("/api/v2/feedback", {"transaction_id": transaction_id, "label": label, "evidence": evidence})

    def export_training(self):
        return self.call("/api/v2/training/export")

    def guard_and_call(self, intent, send_payment, verified_assessment=None):
        """Gate the integrating application's payment function; no processor is imposed.

        Do not auto-retry send_payment on timeout. The integrating app must use a
        provider idempotency key and reconcile uncertain outcomes independently.
        """
        assessment = verified_assessment or self.assess(intent)
        if not assessment.get("permit"):
            return {"attempted": False, "assessment": assessment}
        self.authorize(intent, assessment["permit"])
        # The payment system still owns user consent, bank authorization, and settlement.
        response = send_payment(intent)
        return {"attempted": True, "assessment": assessment, "payment_response": response}

    def close(self):
        self.client.close()

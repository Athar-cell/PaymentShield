from __future__ import annotations

import hashlib
import hmac
import uuid
import httpx


class ProviderError(RuntimeError):
    pass


def valid_signature(secret: str, data: bytes, signature: str) -> bool:
    return bool(secret and signature) and hmac.compare_digest(
        hmac.new(secret.encode(), data, hashlib.sha256).hexdigest(), signature)


class DemoProvider:
    name = "demo"
    key_id = None

    def create_order(self, amount: int, receipt: str) -> dict:
        return {"id": "demo_" + uuid.uuid4().hex, "amount": amount, "currency": "INR"}


class RazorpayTestProvider:
    name = "razorpay_test"

    def __init__(self, key_id: str, secret: str, webhook_secret: str = "", client=None):
        if not key_id.startswith("rzp_test_") or not secret:
            raise ValueError("Razorpay test keys are required. Live payment keys are deliberately disabled.")
        self.key_id, self._secret, self._webhook_secret = key_id, secret, webhook_secret
        self.client = client or httpx.Client(
            base_url="https://api.razorpay.com/v1/", auth=(key_id, secret), timeout=15)

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never echo provider response bodies or credentials to the customer.
            raise ProviderError("Payment provider unavailable. Check the order status before retrying.") from exc

    def create_order(self, amount: int, receipt: str) -> dict:
        result = self._request("POST", "orders", json={"amount": amount, "currency": "INR", "receipt": receipt})
        if result.get("amount") != amount or result.get("currency") != "INR" or not result.get("id"):
            raise ProviderError("Unexpected provider order response.")
        return result

    def verify_payment(self, provider_order_id: str, payment_id: str, signature: str, amount: int) -> dict:
        if not valid_signature(self._secret, f"{provider_order_id}|{payment_id}".encode(), signature):
            raise ValueError("Invalid payment signature.")
        payment = self._request("GET", "payments/" + payment_id)
        if (payment.get("order_id") != provider_order_id or payment.get("amount") != amount
                or payment.get("currency") != "INR"):
            raise ValueError("Payment does not match this order and amount.")
        return payment

    def validate_webhook(self, raw: bytes, signature: str) -> bool:
        return valid_signature(self._webhook_secret, raw, signature)

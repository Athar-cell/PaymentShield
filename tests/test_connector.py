import hashlib
import hmac
import json

from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

from src.connector.api import create_app
from src.connector.features import FEATURES, build_features, DEMO_PROFILES
from src.connector.model import CheckoutRisk
from src.connector.providers import DemoProvider, ProviderError, RazorpayTestProvider, valid_signature
from scripts.train_checkout import choose_thresholds, generate_data


class RiskStub:
    bundle = {"stub": True}
    report = {"source": "test"}

    def assess(self, features):
        from src.connector.features import rule_assessment
        score, reasons = rule_assessment(features)
        return {"score": score, "decision": "stop" if score >= 90 else "review" if score >= 40 else "proceed",
                "reasons": reasons, "note": "Test assessment", "model_available": True}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3", provider=DemoProvider(), risk=RiskStub(), merchant_token="test-merchant-token")) as browser:
        browser.get("/api/v1/config")
        yield browser


def order(client, scenario="returning", key="unique-key-123", item="headphones"):
    response = client.post("/api/v1/orders", json={"item": item, "scenario": scenario, "quantity": 1, "idempotency_key": key})
    assert response.status_code in {200, 201}, response.text
    return response.json()


def test_server_price_and_idempotent_creation(client):
    first = order(client)
    assert first["amount"] == 249900
    assert order(client)["id"] == first["id"]
    bad = client.post("/api/v1/orders", json={"item": "camera", "idempotency_key": "unique-key-123"})
    assert bad.status_code == 409
    assert client.post("/api/v1/orders", json={"amount": 1, "idempotency_key": "new-key-123"}).status_code == 422


@pytest.mark.parametrize("scenario,state", [("returning", "ready"), ("new_customer", "review"), ("unusual", "review"), ("burst", "blocked")])
def test_checkout_is_gated_by_server(client, scenario, state):
    created = order(client, scenario)
    assert created["state"] == state
    response = client.post(f"/api/v1/orders/{created['id']}/checkout")
    assert response.status_code == (200 if state == "ready" else 409)


def test_review_requires_merchant_and_audit_note(client):
    created = order(client, "new_customer")
    endpoint = f"/api/v1/merchant/orders/{created['id']}/review"
    body = {"approve": True, "note": "Verified order independently with customer."}
    assert client.post(endpoint, json=body).status_code == 401
    headers = {"Authorization": "Bearer test-merchant-token"}
    assert client.post(endpoint, json=body, headers=headers).status_code == 200
    assert client.post(f"/api/v1/orders/{created['id']}/checkout").status_code == 200
    events = client.get(f"/api/v1/orders/{created['id']}").json()["events"]
    assert any(row["kind"] == "merchant_review" for row in events)


def test_blocked_order_cannot_be_overridden(client):
    created = order(client, "burst")
    response = client.post(f"/api/v1/merchant/orders/{created['id']}/review", json={"approve": True, "note": "Attempt to bypass hard block."}, headers={"Authorization": "Bearer test-merchant-token"})
    assert response.status_code == 409


def test_demo_payment_and_duplicate_completion(client):
    created = order(client)
    path = f"/api/v1/orders/{created['id']}"
    assert client.post(path + "/demo-complete").status_code == 409
    first = client.post(path + "/checkout").json()
    assert client.post(path + "/checkout").json()["order_id"] == first["order_id"]
    assert client.post(path + "/demo-complete").json()["state"] == "paid"
    assert client.post(path + "/demo-complete?outcome=failure").json()["state"] == "paid"


def test_demo_failure_is_not_risk_rejection(client):
    created = order(client)
    client.post(f"/api/v1/orders/{created['id']}/checkout")
    result = client.post(f"/api/v1/orders/{created['id']}/demo-complete?outcome=failure").json()
    assert result["state"] == "failed"
    assert result["assessment"]["decision"] == "proceed"


def test_customer_isolation_and_cross_origin(client, tmp_path):
    created = order(client)
    client.cookies.clear()
    assert client.get(f"/api/v1/orders/{created['id']}").status_code == 404
    response = client.post("/api/v1/orders", json={"idempotency_key": "key-new-123"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.get("/api/v1/merchant/orders").status_code == 401


def test_order_expiry(client):
    created = order(client)
    with client.app.state.store.transaction() as db:
        db.execute("UPDATE orders SET expires=0 WHERE id=?", (created["id"],))
    assert client.post(f"/api/v1/orders/{created['id']}/checkout").status_code == 409


def test_missing_model_goes_to_review(tmp_path):
    engine = CheckoutRisk(tmp_path)
    result = engine.assess(build_features(249900, 1, DEMO_PROFILES["returning"], 13))
    assert result["decision"] == "review"
    assert result["model_score"] is None


def test_nonfinite_model_output_cannot_approve(tmp_path):
    class BrokenModel:
        def predict_proba(self, frame):
            return np.array([[float("nan"), float("nan")]])
    engine = CheckoutRisk(tmp_path)
    engine.bundle = {"model": BrokenModel(), "thresholds": {"review": .5, "block": .9}, "version": "test"}
    assert engine.assess(build_features(249900, 1, DEMO_PROFILES["returning"], 13))["decision"] == "review"


def test_live_keys_rejected():
    with pytest.raises(ValueError, match="Live"):
        RazorpayTestProvider("rzp_live_example", "secret")


def make_provider(status="captured", amount=249900):
    def handle(request):
        if request.url.path.endswith("orders"):
            body = json.loads(request.content)
            return httpx.Response(200, json={"id": "order_123", "amount": body["amount"], "currency": "INR"})
        return httpx.Response(200, json={"id": "pay_123", "order_id": "order_123", "amount": amount, "currency": "INR", "status": status})
    client = httpx.Client(base_url="https://api.razorpay.com/v1/", transport=httpx.MockTransport(handle))
    return RazorpayTestProvider("rzp_test_example", "test-secret", "webhook-secret", client)


def signed_data(order_id="order_123", payment_id="pay_123"):
    return {"razorpay_order_id": order_id, "razorpay_payment_id": payment_id,
            "razorpay_signature": hmac.new(b"test-secret", f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()}


def test_provider_signature_and_amount_checks():
    provider = make_provider()
    assert provider.verify_payment("order_123", "pay_123", signed_data()["razorpay_signature"], 249900)["status"] == "captured"
    with pytest.raises(ValueError):
        provider.verify_payment("order_123", "pay_123", "0" * 64, 249900)
    with pytest.raises(ValueError):
        make_provider(amount=1).verify_payment("order_123", "pay_123", signed_data()["razorpay_signature"], 249900)
    assert not valid_signature("secret", b"body", "fake")


def external_order(browser):
    browser.get("/api/v1/config")
    created = order(browser)
    assert created["state"] == "review"  # Cannot trust browser-selected demo profile in test-provider mode.
    browser.post(f"/api/v1/merchant/orders/{created['id']}/review", json={"approve": True, "note": "Independently confirmed this test order."}, headers={"Authorization": "Bearer merchant"})
    assert browser.post(f"/api/v1/orders/{created['id']}/checkout").status_code == 200
    return created


@pytest.mark.parametrize("provider_status,state", [("authorized", "authorized"), ("captured", "paid")])
def test_external_provider_result_requires_server_confirmation(tmp_path, provider_status, state):
    with TestClient(create_app(tmp_path / "db", make_provider(status=provider_status), RiskStub(), "merchant")) as browser:
        created = external_order(browser)
        assert browser.post(f"/api/v1/orders/{created['id']}/demo-complete").status_code == 404
        result = browser.post(f"/api/v1/orders/{created['id']}/verify", json=signed_data())
        assert result.status_code == 200
        assert result.json()["state"] == state


def webhook_body(kind="captured", amount=249900):
    return json.dumps({"event": "payment." + kind, "payload": {"payment": {"entity": {
        "id": "pay_123", "order_id": "order_123", "amount": amount, "currency": "INR", "status": kind}}}}).encode()


def post_webhook(browser, raw, signature=None):
    signature = signature or hmac.new(b"webhook-secret", raw, hashlib.sha256).hexdigest()
    return browser.post("/api/v1/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": signature, "Content-Type": "application/json"})


def test_webhook_signature_duplicates_amount_and_out_of_order(tmp_path):
    with TestClient(create_app(tmp_path / "db", make_provider(), RiskStub(), "merchant")) as browser:
        created = external_order(browser)
        assert post_webhook(browser, webhook_body(), "bad").status_code == 400
        assert post_webhook(browser, webhook_body(amount=1)).status_code == 400
        assert post_webhook(browser, webhook_body()).status_code == 200
        assert post_webhook(browser, webhook_body()).json()["duplicate"]
        assert post_webhook(browser, webhook_body("authorized")).status_code == 200
        assert post_webhook(browser, webhook_body("failed")).status_code == 200
        assert browser.get(f"/api/v1/orders/{created['id']}").json()["state"] == "paid"


def test_uncertain_provider_creation_is_not_retried(tmp_path):
    class UncertainProvider(DemoProvider):
        calls = 0
        def create_order(self, amount, receipt):
            self.calls += 1
            raise ProviderError("timeout")
    provider = UncertainProvider()
    with TestClient(create_app(tmp_path / "db", provider, RiskStub())) as browser:
        browser.get("/api/v1/config")
        created = order(browser)
        path = f"/api/v1/orders/{created['id']}/checkout"
        assert browser.post(path).status_code == 502
        assert browser.post(path).status_code == 409
        assert provider.calls == 1


def test_checkout_training_contract_and_no_split_overlap():
    data = generate_data(users=100)
    groups = {key: set(part.user_id) for key, part in data.groupby("split")}
    assert not groups["train"] & groups["test"]
    assert not groups["train"] & groups["validation"]
    assert not groups["validation"] & groups["test"]
    assert set(FEATURES) <= set(data)
    assert not set(FEATURES) & {"label_fraud", "user_id", "split", "timestamp"}
    assert 0 < data.label_fraud.mean() < .5


def test_threshold_selection_does_not_force_model_only_block():
    thresholds, curve = choose_thresholds(np.array([0, 0, 1, 0, 1]), np.array([.1, .2, .3, .4, .5]))
    assert thresholds["block"] > 1
    selected = next(row for row in curve if row["threshold"] == thresholds["review"])
    assert selected["false_positive_rate"] <= .05

import base64
import hashlib
import hmac
import io
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import httpx
import pytest
import qrcode

from src.live.api import create_app, signing_message
from src.live.client import PaymentShieldClient
from src.live.engine import assess, language_flags

SECRET = "unit-test-only-signing-fixture-0123456789"


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path / "live.sqlite3", "test-app", SECRET, "operator-fixture")
    with TestClient(app) as browser:
        sdk = PaymentShieldClient("http://testserver", "test-app", SECRET, transport=browser)
        yield browser, sdk, app


def intent(tx="payment_0001", **extra):
    return {"transaction_id": tx, "customer_ref": "customer_001", "amount_paise": 25000,
            "payee": "shop@example", "expected_payee": "shop@example", "expected_amount_paise": 25000,
            "device_ref": "device_001", **extra}


def signed(browser, path, body, *, timestamp=None, nonce=None, sign_path=None):
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp, nonce = timestamp or str(int(time.time())), nonce or secrets.token_hex(16)
    signature = hmac.new(SECRET.encode(), signing_message("POST", sign_path or path, timestamp, nonce, raw), hashlib.sha256).hexdigest()
    headers = {"X-PS-Key-Id": "test-app", "X-PS-Timestamp": timestamp, "X-PS-Nonce": nonce,
               "X-PS-Signature": signature, "Content-Type": "application/json"}
    return browser.post(path, content=raw, headers=headers)


def seed_paid(sdk, count=3, environment="test"):
    for i in range(count):
        value = intent(f"warmup_{environment}_{i}", environment=environment)
        sdk.assess(value)
        cleared = sdk.independently_verified(value["transaction_id"], "Unit fixture: independently checked test payment context.")
        sdk.authorize(value, cleared["permit"])
        sdk.record_outcome(value["transaction_id"], f"event_{environment}_{i}", "paid", f"fixture_provider_{i}")


def test_unknown_history_is_verify_not_zero_score_approval(setup):
    browser, sdk, _ = setup
    result = sdk.assess(intent())
    assert result["risk_index"] == 0
    assert result["decision"] == "verify"
    assert result["missing_evidence"]
    assert result["ml"]["mode"] in {"disabled", "active"}
    assert browser.get("/api/v2/status").json()["bank_feed_connected"] is False


@pytest.mark.parametrize("changes,code", [
    ({"payee": "other@example"}, "payee_changed"),
    ({"amount_paise": 30000}, "amount_changed"),
    ({"purpose": "receive_refund"}, "refund_as_debit"),
    ({"message": "Please share your UPI PIN with the support agent."}, "credential_sharing"),
    ({"message": "Approve this payment to receive your refund."}, "refund_as_debit"),
])
def test_concrete_unsafe_details_stop_without_model(setup, changes, code):
    _, sdk, _ = setup
    result = sdk.assess(intent(**changes))
    assert result["decision"] == "stop"
    assert result["permit"] is None
    assert code in {signal["code"] for signal in result["signals"]}
    with pytest.raises(httpx.HTTPStatusError):
        sdk.independently_verified("payment_0001", "Attempting to override a stop is not permitted.")


@pytest.mark.parametrize("message", ["Enter your UPI PIN inside your trusted payment app.",
                                          "Never share your OTP or UPI PIN with anyone.",
                                          "Do not share your OTP."])
def test_normal_pin_entry_and_safety_advice_are_not_credential_requests(message):
    assert language_flags(message)["credential_request"] is False


def test_separate_unsafe_sentence_is_not_suppressed_by_advice():
    assert language_flags("Never share your OTP. Please send your OTP to me.")["credential_request"]


def test_authentication_nonce_timestamp_path_and_body_binding(setup):
    browser, _, _ = setup
    assert browser.post("/api/v2/assess", json=intent()).status_code == 401
    assert signed(browser, "/api/v2/assess", intent(), timestamp=str(int(time.time()) - 120)).status_code == 401
    assert signed(browser, "/api/v2/assess", intent(), sign_path="/api/v2/outcomes").status_code == 401
    nonce = secrets.token_hex(16)
    assert signed(browser, "/api/v2/assess", intent(), nonce=nonce).status_code == 200
    assert signed(browser, "/api/v2/assess", intent(), nonce=nonce).status_code == 409


def test_strict_contract_rejects_invented_history_and_invalid_amount(setup):
    browser, _, _ = setup
    assert signed(browser, "/api/v2/assess", intent(amount_paise=True)).status_code == 422
    assert signed(browser, "/api/v2/assess", intent(amount_paise=0)).status_code == 422
    assert signed(browser, "/api/v2/assess", intent(paid_history_count=100)).status_code == 422


def test_idempotency_conflict_and_no_duplicate_history(setup):
    _, sdk, _ = setup
    first = sdk.assess(intent())
    again = sdk.assess(intent())
    assert first["assessment_id"] == again["assessment_id"]
    assert again["idempotent"]
    with pytest.raises(httpx.HTTPStatusError):
        sdk.assess(intent(amount_paise=26000))
    next_result = sdk.assess(intent("payment_0002"))
    assert next_result["evidence"]["previous_attempts_10m"] == 1


def test_recorded_history_can_support_proceed_and_single_use_permit(setup):
    _, sdk, _ = setup
    seed_paid(sdk)
    value = intent("subsequent_0001")
    result = sdk.assess(value)
    assert result["decision"] == "proceed"
    assert result["evidence"]["paid_history_count"] == 3
    assert result["permit"]
    assert sdk.assess(value)["permit"] == result["permit"]
    assert sdk.authorize(value, result["permit"])["permitted"]
    with pytest.raises(httpx.HTTPStatusError):
        sdk.authorize(value, result["permit"])


def test_permit_cannot_be_reused_for_changed_amount_or_payee(setup):
    _, sdk, _ = setup
    value = intent()
    sdk.assess(value)
    result = sdk.independently_verified(value["transaction_id"], "Test verification of the exact payment and recipient.")
    for changes in [{"amount_paise": 25001}, {"payee": "other@example"}]:
        with pytest.raises(httpx.HTTPStatusError):
            sdk.authorize({**value, **changes}, result["permit"])


def test_permit_expiry_and_simultaneous_consumption(setup):
    _, sdk, app = setup
    value = intent()
    result = sdk.assess(value)
    cleared = sdk.independently_verified(value["transaction_id"], "Independent verification test fixture.")
    def consume():
        try:
            sdk.authorize(value, cleared["permit"])
            return True
        except httpx.HTTPStatusError:
            return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sum(workers.map(lambda _: consume(), range(2))) == 1
    other = intent("payment_0002")
    result = sdk.assess(other)
    cleared = sdk.independently_verified(other["transaction_id"], "Independent verification test fixture.")
    with app.state.store.transaction() as db:
        db.execute("UPDATE live_assessments SET permit_expires=0 WHERE id=?", (cleared["assessment_id"],))
    with pytest.raises(httpx.HTTPStatusError):
        sdk.authorize(other, cleared["permit"])


def test_new_evidence_revokes_old_permit(setup):
    _, sdk, _ = setup
    seed_paid(sdk)
    value = intent("old_permit_0001")
    result = sdk.assess(value)
    assert result["permit"]
    for i in range(3):
        other = intent(f"later_attempt_{i}")
        sdk.assess(other)
        sdk.record_outcome(other["transaction_id"], f"failure_event_{i}", "failed", f"provider_fail_{i}")
    with pytest.raises(httpx.HTTPStatusError, match="409"):
        sdk.authorize(value, result["permit"])
    assert sdk.assess(value)["permit"] is None


def test_sdk_does_not_execute_payment_for_stop_or_verify(setup):
    _, sdk, _ = setup
    called = []
    for value in [intent(), intent("bad_payment_0001", payee="changed@example")]:
        result = sdk.guard_and_call(value, lambda payload: called.append(payload))
        assert result["attempted"] is False
    assert not called


def test_sdk_executes_callback_only_after_independent_clearance(setup):
    _, sdk, _ = setup
    value = intent()
    sdk.assess(value)
    cleared = sdk.independently_verified(value["transaction_id"], "Test-only independent customer verification performed.")
    called = []
    result = sdk.guard_and_call(value, lambda payload: called.append(payload) or {"test": True}, cleared)
    assert result["attempted"] is True
    assert len(called) == 1


def test_test_history_never_becomes_live_customer_history(setup):
    _, sdk, _ = setup
    seed_paid(sdk)
    result = sdk.assess(intent("real_namespace_0001", environment="live"))
    assert result["decision"] == "verify"
    assert result["evidence"]["paid_history_count"] == 0


def test_outcome_idempotency_conflicting_outcome_and_reference(setup):
    _, sdk, _ = setup
    sdk.assess(intent())
    assert not sdk.record_outcome("payment_0001", "event_0001", "paid", "provider_0001")["bank_verified"]
    assert sdk.record_outcome("payment_0001", "event_0001", "paid", "provider_0001")["duplicate"]
    for event,status,ref in [("event_0001","failed","provider_0001"), ("event_0002","failed","provider_0001"), ("event_0003","paid","provider_changed")]:
        with pytest.raises(httpx.HTTPStatusError):
            sdk.record_outcome("payment_0001", event, status, ref)


def test_labels_not_payment_status_drive_training_export_and_privacy(setup):
    browser, sdk, app = setup
    value = intent("observed_live_0001", environment="live", message="Private narrative should not be retained.")
    result = sdk.assess(value)
    sdk.record_outcome(value["transaction_id"], "paid_event_0001", "paid", "provider_0001")
    assert sdk.export_training()["rows"] == []
    sdk.record_label(value["transaction_id"], 1, "Independent adjudication evidence fixture with no personal details.")
    exported = sdk.export_training()
    assert len(exported["rows"]) == 1
    assert exported["rows"][0]["label_fraud"] == 1
    assert exported["rows"][0]["paid_history_count"] == 0  # assessment-time snapshot
    assert "Private narrative" not in json.dumps(exported)
    with app.state.store.transaction() as db:
        text = str([tuple(row) for row in db.execute("SELECT * FROM live_assessments")])
        assert "shop@example" not in text
        assert "Private narrative" not in text
    # A recipient from a later adjudicated-fraud example is not trusted as normal history.
    next_result = sdk.assess(intent("observed_live_0002", environment="live"))
    assert next_result["evidence"]["paid_history_count"] == 0


def test_manual_checks_never_issue_a_permit(setup):
    browser, _, _ = setup
    result = browser.post("/api/v2/check", json={"amount_paise":25000,"payee":"shop@example","expected_payee":"shop@example"}).json()
    assert result["decision"] == "verify"
    assert "permit" not in result
    assert result["evidence"]["source"] == "user_supplied"


@pytest.mark.parametrize("payload", ["https://example.com", "upi://pay?pa=shop@example&pa=changed@example", "upi://pay/path?pa=shop@example", "upi://pay?pa=shop@example&am="+'9'*400])
def test_invalid_ambiguous_or_unbounded_qr_is_rejected(setup, payload):
    browser, _, _ = setup
    response = browser.post("/api/v2/check-qr", json={"payload":payload,"amount_paise":25000})
    assert response.status_code == 422


def test_qr_amount_and_expected_recipient_check(setup):
    browser, _, _ = setup
    result=browser.post("/api/v2/check-qr",json={"payload":"upi://pay?pa=other@example&am=300&pn=Shop","amount_paise":25000,"expected_payee":"shop@example"}).json()
    assert result["decision"] == "stop"
    assert result["qr"]["name_verified"] is False
    assert {s["code"] for s in result["signals"]} >= {"amount_changed","payee_changed"}


def test_real_qr_image_decode_and_refund_context(setup):
    browser, _, _ = setup
    buffer=io.BytesIO()
    qrcode.make("upi://pay?pa=shop@example&am=250&pn=Example").save(buffer,format="PNG")
    result=browser.post("/api/v2/check-qr-image",json={"image_base64":base64.b64encode(buffer.getvalue()).decode(),"amount_paise":25000,"purpose":"receive_refund"})
    assert result.status_code==200
    assert result.json()["decision"] == "stop"
    assert result.json()["qr"]["payee"] == "shop@example"
    assert browser.post("/api/v2/check-qr-image",json={"image_base64":"notbase64!","amount_paise":25000}).status_code==422


def test_console_auth_cookie_data_and_no_synthetic_report(setup):
    browser, sdk, _ = setup
    assert browser.get("/api/v2/console/state").status_code==401
    login=browser.post("/api/v2/console/session",headers={"Authorization":"Bearer operator-fixture"})
    assert login.status_code==200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert browser.get("/api/v2/console/state").json()["assessments"]==[]
    sdk.assess(intent())
    state=browser.get("/api/v2/console/state").json()
    assert state["counts"]=={"test":1}
    assert state["assessments"][0]["permit"] is None
    assert browser.get("/api/v2/console/model").json()["available"] is False
    assert browser.post("/api/v2/check",json={"amount_paise":25000,"payee":"shop@example"},headers={"Origin":"https://evil.example"}).status_code==403


def test_missing_app_secret_cannot_accept_signed_events(tmp_path):
    with TestClient(create_app(tmp_path/"empty.sqlite3","test-app","","")) as browser:
        assert browser.get("/api/v2/status").json()["integration_configured"] is False
        assert browser.post("/api/v2/assess",json=intent()).status_code==503

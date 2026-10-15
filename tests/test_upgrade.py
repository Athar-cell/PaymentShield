import io

import numpy as np
import pandas as pd
import pytest
import qrcode

from src.fraudshield.data import TRANSACTION_FEATURES, read_dataset
from src.fraudshield.qr import decode_qr_image, merchant_names_match, parse_upi_payload
from src.fraudshield.risk_engine import analyze_payment, text_signals
from src.fraudshield.scenarios import SCENARIOS, scenario_features
from src.fraudshield.training import audit_dataset, filter_realistic_rows, message_group_splits


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_showcase_works_without_models(name):
    values = SCENARIOS[name]
    features, qr = scenario_features(values)
    result = analyze_payment(features, values["message"], qr)
    assert result["risk_band"] == values["expected_band"]


class LowModel:
    def predict_proba(self, frame):
        return np.array([[0.99, 0.01]])


def test_models_cannot_dilute_explicit_refund_warning():
    values = SCENARIOS["Refund trap"]
    features, qr = scenario_features(values)
    result = analyze_payment(features, values["message"], qr, LowModel(), LowModel())
    assert result["risk_score"] >= 85
    assert result["rule_floor"] == 85


def test_shopping_does_not_match_pin_or_otp():
    flags = text_signals("Shopping for groceries at the usual store")
    assert not flags["asks_for_upi_pin"]
    assert not flags["asks_for_otp"]


def test_security_advice_is_not_a_request_for_otp():
    flags = text_signals("Never share your OTP or UPI PIN with anyone.")
    assert not flags["asks_for_upi_pin"]
    assert not flags["asks_for_otp"]


def test_unknown_qr_name_is_not_assumed_to_match():
    assert merchant_names_match("DMart", "") is None
    assert merchant_names_match("Demo Merchant", "demo merchant") is True


@pytest.mark.parametrize("payload", [
    "https://example.com",
    "upi://pay?pa=bad id@upi",
    "upi://pay?pa=a@b&pa=c@d",
    "upi://pay?pa=a@b&am=-4",
    "upi://pay?pa=a@b&cu=USD",
])
def test_invalid_or_ambiguous_qr_is_rejected(payload):
    with pytest.raises(ValueError):
        parse_upi_payload(payload)


def test_qr_image_roundtrip():
    image = qrcode.make("upi://pay?pa=demo@example&pn=Demo%20Merchant&am=250&cu=INR")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = decode_qr_image(buffer.getvalue())
    assert result["payee_name"] == "Demo Merchant"
    assert result["amount_inr"] == 250


def test_data_complete_and_no_label_leakage():
    tx, messages, qr = read_dataset("data")
    audit = audit_dataset(tx, messages, qr)
    assert (len(tx), len(messages), len(qr)) == (6004, 2534, 1451)
    assert audit["user_split_overlap"] == 0
    assert not set(TRANSACTION_FEATURES) & {"label_fraud", "fraud_category", "message_category", "user_id", "transaction_id", "source_type"}
    parts = message_group_splits(filter_realistic_rows(messages))
    groups = {name: set(frame["_text_group"]) for name, frame in parts.items()}
    assert not groups["train"] & groups["test"]
    assert not groups["train"] & groups["validation"]
    assert not groups["validation"] & groups["test"]

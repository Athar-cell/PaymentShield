from src.fraudshield.risk_engine import analyze_payment


def base_features() -> dict:
    return {
        "amount_inr": 250,
        "hour": 13,
        "receiver_known": True,
        "receiver_age_days": 500,
        "merchant_match": True,
        "new_device": False,
        "location_changed": False,
        "device_trust_score": 95,
        "velocity_1h": 1,
        "account_age_days": 700,
        "prior_txn_count": 60,
        "city": "Bengaluru",
        "state": "Karnataka",
        "upi_app": "Google Pay",
        "device_type": "android_personal",
        "payment_type": "pay",
        "channel": "in_app",
        "day_of_week": "Mon",
        "message_present": True,
        "message_category": "legitimate",
        "urgency_score": 5,
        "asks_for_otp": False,
        "asks_for_upi_pin": False,
        "asks_to_scan_qr": False,
        "asks_to_install_app": False,
        "qr_scanned": False,
        "qr_merchant_match": True,
        "amount_match": True,
    }


def test_fake_refund_is_high_risk():
    features = base_features()
    features.update({"amount_inr": 5000, "receiver_known": False, "payment_type": "collect_request"})
    result = analyze_payment(features, "Your refund is ready. Approve the collect request to receive it.")
    assert result["risk_band"] == "high"
    assert any("collect request" in reason.lower() for reason in result["reasons"])


def test_qr_mismatch_is_high_risk():
    features = base_features()
    features.update({"amount_inr": 25000, "qr_scanned": True, "qr_merchant_match": False})
    result = analyze_payment(features, "Scan this QR to receive your cashback.", {"sticker_tampered": True})
    assert result["risk_band"] == "high"
    assert any("merchant" in reason.lower() for reason in result["reasons"])


def test_normal_payment_is_low_risk():
    result = analyze_payment(base_features(), "Lunch payment to Rahul")
    assert result["risk_band"] == "low"

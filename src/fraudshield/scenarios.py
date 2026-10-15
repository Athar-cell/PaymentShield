SCENARIOS = {
    "Refund trap": {
        "amount": 5000.0, "payment_type": "collect_request", "receiver_known": False,
        "new_device": True, "location_changed": False, "device_trust": 18,
        "expected": "Amazon", "decoded": "Amazon", "sticker_tampered": False,
        "message": "Your refund of Rs 5000 is ready. Approve the collect request to receive it.",
        "expected_band": "high", "story": "A promise of incoming money hides an outgoing payment request.",
    },
    "KYC impersonation": {
        "amount": 1.0, "payment_type": "collect_request", "receiver_known": False,
        "new_device": True, "location_changed": True, "device_trust": 12,
        "expected": "SBI", "decoded": "SBI", "sticker_tampered": False,
        "message": "Urgent KYC verification. Install AnyDesk and enter your UPI PIN to keep your account active.",
        "expected_band": "high", "story": "Account-pressure language is combined with remote access and a PIN request.",
    },
    "Replaced QR": {
        "amount": 25000.0, "payment_type": "qr", "receiver_known": False,
        "new_device": False, "location_changed": False, "device_trust": 64,
        "expected": "DMart", "decoded": "Personal UPI", "sticker_tampered": True,
        "message": "Scan this QR to receive your cashback. Enter your UPI PIN to verify.",
        "expected_band": "high", "story": "The QR points at a different payee and claims a PIN is needed to receive cashback.",
    },
    "Lunch with a friend": {
        "amount": 250.0, "payment_type": "pay", "receiver_known": True,
        "new_device": False, "location_changed": False, "device_trust": 95,
        "expected": "Cafe Coffee Day", "decoded": "Cafe Coffee Day", "sticker_tampered": False,
        "message": "Lunch payment to Rahul", "expected_band": "low",
        "story": "A familiar receiver, a normal amount, and an ordinary message.",
    },
}


def scenario_features(values: dict) -> tuple[dict, dict]:
    from .qr import merchant_names_match
    from .risk_engine import text_signals
    match = merchant_names_match(values.get("expected", ""), values.get("decoded", ""))
    features = {
        "amount_inr": values["amount"], "hour": values.get("hour", 13),
        "receiver_known": values["receiver_known"],
        "receiver_age_days": values.get("receiver_age_days", 10 if not values["receiver_known"] else 500),
        "merchant_match": match, "new_device": values["new_device"],
        "location_changed": values["location_changed"], "device_trust_score": values["device_trust"],
        "velocity_1h": values.get("velocity_1h", 1), "account_age_days": 500, "prior_txn_count": 30,
        "city": "Bengaluru", "state": "Karnataka", "upi_app": "Google Pay",
        "device_type": "android_personal", "payment_type": values["payment_type"],
        "channel": values.get("channel", "in_app"), "day_of_week": "Mon",
        "message_present": bool(values["message"].strip()),
        "qr_scanned": values["payment_type"] == "qr",
        "qr_merchant_match": match if values["payment_type"] == "qr" else None,
        "amount_match": values.get("amount_match") if values["payment_type"] == "qr" else None,
        **text_signals(values["message"]),
    }
    return features, {"sticker_tampered": values.get("sticker_tampered", False)}

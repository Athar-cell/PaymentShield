from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .data import TRANSACTION_FEATURES, clean_transaction_features


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 1)


def rule_score(features: dict[str, Any], message_text: str, qr_info: dict[str, Any]) -> tuple[float, list[str]]:
    text = (message_text or "").lower()
    score = 0.0
    reasons: list[str] = []

    payment_type = str(features.get("payment_type", "")).lower()
    amount = float(features.get("amount_inr", 0) or 0)
    receiver_known = _as_bool(features.get("receiver_known", False))

    if payment_type == "collect_request" and re.search(r"refund|cashback|receive|reward", text):
        score += 35
        reasons.append("A collect request is paired with refund or reward wording.")
    if _as_bool(features.get("asks_for_upi_pin")) or re.search(r"upi\s*pin|pin", text):
        score += 28
        reasons.append("The message asks for a UPI PIN, which should never be shared to receive money.")
    if _as_bool(features.get("asks_for_otp")) or re.search(r"\botp\b|one[- ]time password", text):
        score += 24
        reasons.append("The message asks for an OTP or one-time password.")
    if _as_bool(features.get("asks_to_install_app")) or re.search(r"anydesk|remote access|screen sharing|install the app", text):
        score += 28
        reasons.append("The message requests remote-access software or screen sharing.")
    if _as_bool(features.get("qr_scanned")) and not _as_bool(features.get("qr_merchant_match", True)):
        score += 35
        reasons.append("The decoded QR merchant does not match the expected merchant.")
    if _as_bool(qr_info.get("sticker_tampered")):
        score += 22
        reasons.append("The QR source is marked as potentially replaced or tampered.")
    if not receiver_known and amount >= 10_000:
        score += 15
        reasons.append("A high-value payment is going to an unknown receiver.")
    if _as_bool(features.get("new_device")) and _as_bool(features.get("location_changed")):
        score += 12
        reasons.append("The payment comes from a new device and a changed location.")
    if float(features.get("urgency_score", 0) or 0) >= 75 or re.search(r"urgent|immediately|today|blocked|suspended", text):
        score += 10
        reasons.append("The message uses urgency or account-threat language.")
    return _clamp(score), reasons


def analyze_payment(
    features: dict[str, Any],
    message_text: str = "",
    qr_info: dict[str, Any] | None = None,
    transaction_model: Any | None = None,
    message_model: Any | None = None,
) -> dict[str, Any]:
    """Return a calibrated prototype score plus human-readable reasons."""
    qr_info = qr_info or {}
    prepared = {column: features.get(column) for column in TRANSACTION_FEATURES}
    prepared["message_present"] = bool(message_text.strip())
    prepared["message_category"] = prepared.get("message_category") or "unknown"
    prepared["qr_merchant_match"] = prepared.get("qr_merchant_match")
    prepared["amount_match"] = prepared.get("amount_match")
    frame = clean_transaction_features(pd.DataFrame([prepared]))

    transaction_probability = 0.0
    if transaction_model is not None:
        transaction_probability = float(transaction_model.predict_proba(frame)[0, 1] * 100)

    message_probability = 0.0
    if message_model is not None and message_text.strip():
        message_probability = float(message_model.predict_proba([message_text])[0, 1] * 100)

    rules, reasons = rule_score(prepared, message_text, qr_info)
    if transaction_model is None and message_model is None:
        final_score = rules
    else:
        final_score = _clamp(0.55 * transaction_probability + 0.25 * message_probability + 0.20 * rules)

    if final_score >= 70:
        band = "high"
        recommendation = "Do not approve. Verify the recipient through an independent channel."
    elif final_score >= 40:
        band = "medium"
        recommendation = "Pause and verify the recipient, amount, and payment request before approving."
    else:
        band = "low"
        recommendation = "No strong risk signal detected. Review the recipient and amount as usual."

    if not reasons and band == "low":
        reasons.append("No high-confidence scam signal was detected by the current rules.")

    return {
        "risk_score": final_score,
        "risk_band": band,
        "recommendation": recommendation,
        "reasons": reasons,
        "components": {
            "transaction_model": round(transaction_probability, 1),
            "message_model": round(message_probability, 1),
            "rule_score": round(rules, 1),
        },
    }


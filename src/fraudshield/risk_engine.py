from __future__ import annotations

import re
from typing import Any

import pandas as pd
from .data import TRANSACTION_FEATURES, clean_transaction_features


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def text_signals(text: str) -> dict[str, bool]:
    """Recognize requests in context, with word boundaries and safety-message exclusions."""
    text = (text or "").casefold()
    safety_advice = bool(re.search(r"\b(?:never|do not|don't)\s+(?:ever\s+)?share\b", text))
    otp = bool(re.search(r"\b(?:otp|one[- ]time password|six digit)\b", text)) and not safety_advice
    pin = bool(re.search(r"\bupi\s*pin\b|\bshare\s+(?:your\s+)?pin\b", text)) and not safety_advice
    remote = bool(re.search(r"\b(?:anydesk|remote access|screen shar(?:e|ing)|install|download)\b", text))
    urgent = bool(re.search(r"\b(?:urgent|immediately|blocked|suspended|expires today)\b", text))
    return {"asks_for_otp": otp, "asks_for_upi_pin": pin,
            "asks_to_install_app": remote, "asks_to_scan_qr": bool(re.search(r"\bqr\b|\bscan\b", text)),
            "urgency_score": 90 if urgent else 10}


def _signals(features: dict, message: str, qr_info: dict) -> list[dict]:
    text = (message or "").casefold()
    extracted = text_signals(message)
    flags = {key: _as_bool(features.get(key)) or _as_bool(extracted.get(key))
             for key in ("asks_for_otp", "asks_for_upi_pin", "asks_to_install_app")}
    signals = []

    def add(title: str, detail: str, points: int, floor: int = 0):
        signals.append({"title": title, "detail": detail, "points": points, "floor": floor})

    if features.get("payment_type") == "collect_request" and re.search(r"\b(?:refund|cashback|receive|reward)\b", text):
        add("Refund requested as a debit", "A collect request asks you to send money. That conflicts with the promise that you will receive a refund.", 45, 85)
    if flags["asks_for_upi_pin"]:
        add("UPI PIN requested", "Keep your PIN private. Entering a PIN can authorize a debit; receiving money does not require sharing it.", 35, 80)
    if flags["asks_for_otp"]:
        add("OTP requested", "The message requests a security code. Verify the sender independently and never disclose the code to an agent.", 35, 80)
    if flags["asks_to_install_app"]:
        add("App or remote-access request", "The message asks for an app, remote access, or screen sharing in a payment conversation. Verify its purpose independently.", 25, 65)
    if _as_bool(features.get("qr_scanned")) and features.get("qr_merchant_match") is not None and not _as_bool(features.get("qr_merchant_match")):
        add("QR merchant mismatch", "The decoded payee name differs from the merchant you expected. Names alone are not identity proof; verify the bank-displayed name and UPI ID.", 40, 70)
    if _as_bool(features.get("qr_scanned")) and features.get("amount_match") is not None and not _as_bool(features.get("amount_match")):
        add("QR amount mismatch", "The amount encoded in the QR differs from your intended payment amount.", 30, 70)
    if _as_bool(qr_info.get("sticker_tampered")):
        add("Possible replaced sticker", "You marked the sticker as suspicious. Confirm the payment details directly with the merchant.", 25, 60)
    if not _as_bool(features.get("receiver_known")) and float(features.get("amount_inr") or 0) >= 10000:
        add("Large payment to a new receiver", "This is at least INR 10,000 and the receiver has no known history in this example.", 15)
    if _as_bool(features.get("new_device")) and _as_bool(features.get("location_changed")):
        add("Device and location changed", "Two context changes make independent verification worthwhile.", 12)
    if extracted["urgency_score"] >= 75:
        add("Pressure to act quickly", "Urgency or account-threat wording can discourage you from checking the request.", 10)
    return signals


def rule_score(features: dict, message_text: str, qr_info: dict) -> tuple[float, list[str]]:
    signals = _signals(features, message_text, qr_info)
    score = max([min(100, sum(s["points"] for s in signals))] + [s["floor"] for s in signals])
    return float(score), [s["detail"] for s in signals]


def analyze_payment(features: dict, message_text: str = "", qr_info: dict | None = None,
                    transaction_model: Any = None, message_model: Any = None) -> dict:
    """Heuristic risk index; it is not a calibrated likelihood of real-world fraud."""
    qr_info = qr_info or {}
    extracted = text_signals(message_text)
    prepared = {column: features.get(column) for column in TRANSACTION_FEATURES}
    prepared["message_present"] = bool(message_text.strip())
    for name, value in extracted.items():
        if name.startswith("asks_"):
            prepared[name] = _as_bool(prepared.get(name)) or bool(value)
    prepared["urgency_score"] = max(float(prepared.get("urgency_score") or 0), extracted["urgency_score"])
    signals = _signals(prepared, message_text, qr_info)
    rules, reasons = rule_score(prepared, message_text, qr_info)
    tx_score = msg_score = None
    weighted = [(rules, 0.20)]
    if transaction_model is not None:
        tx_score = float(transaction_model.predict_proba(clean_transaction_features(pd.DataFrame([prepared])))[0, 1] * 100)
        weighted.append((tx_score, 0.55))
    if message_model is not None and message_text.strip():
        msg_score = float(message_model.predict_proba([message_text])[0, 1] * 100)
        weighted.append((msg_score, 0.25))
    blended = sum(score * weight for score, weight in weighted) / sum(weight for _, weight in weighted)
    # Strong, explicit warning signals must not disappear when an ML model disagrees.
    floor = max([0] + [s["floor"] for s in signals])
    final_score = round(min(100, max(blended, floor)), 1)
    band = "high" if final_score >= 70 else "medium" if final_score >= 40 else "low"
    actions = {
        "high": "Pause this payment. Verify the recipient and request through a trusted, independent channel.",
        "medium": "Check the receiver, amount, and request before approving.",
        "low": "No strong warning detected. Still confirm the receiver and amount in your payment app.",
    }
    if tx_score is not None and tx_score >= 60:
        reasons.append("The behavioral model found a pattern resembling the synthetic fraud examples.")
    if msg_score is not None and msg_score >= 60:
        reasons.append("The language model found wording resembling the synthetic scam examples.")
    if not reasons:
        reasons = ["No strong warning was found by the available checks. This does not guarantee a safe payment."]
    return {
        "risk_score": final_score, "risk_band": band, "recommendation": actions[band],
        "reasons": reasons, "signals": signals, "rule_floor": floor,
        "components": {"transaction_model": round(tx_score, 1) if tx_score is not None else None,
                       "message_model": round(msg_score, 1) if msg_score is not None else None,
                       "rule_score": rules},
        "mode": "hybrid" if transaction_model is not None and message_model is not None else "partial" if len(weighted) > 1 else "rules only",
        "score_note": "Prototype risk index, not a calibrated fraud probability.",
    }

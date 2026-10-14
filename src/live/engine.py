"""Explain observed warning signals and compute real-time machine learning trust scores."""
from __future__ import annotations

import math
import os
from pathlib import Path
import pickle
import re

VERSION = "paymentshield-live-1"
ML_FEATURES = ["amount_paise", "attempts_10m", "failed_1h", "paid_history_count",
               "amount_ratio", "new_device", "recipient_seen", "payee_mismatch",
               "amount_mismatch", "credential_request", "refund_debit_conflict", "remote_access_request"]

_MODELS = None


def _get_ml_models():
    global _MODELS
    if _MODELS is not None:
        return _MODELS
    models = {"message": None, "transaction": None}
    root = Path(__file__).resolve().parents[2]
    art = root / "artifacts"
    msg_path = art / "message_model.pkl"
    tx_path = art / "transaction_model.pkl"
    if msg_path.is_file():
        try:
            with open(msg_path, "rb") as handle:
                models["message"] = pickle.load(handle)
        except Exception:
            pass
    if tx_path.is_file():
        try:
            with open(tx_path, "rb") as handle:
                models["transaction"] = pickle.load(handle)
        except Exception:
            pass
    _MODELS = models
    return _MODELS


def language_flags(message: str) -> dict:
    text = message.casefold()
    # Sharing credentials is different from entering a PIN inside the trusted payment app.
    credential = bool(re.search(r"\b(?:share|tell|send|disclose|provide)\b.{0,45}\b(?:otp|upi\s*pin|password)\b", text))
    negated = bool(re.search(r"\b(?:never|do not|don't)\s+(?:ever\s+)?(?:share|tell|send|disclose|provide)\b", text))
    if negated:
        # Do not accidentally suppress a later, separate imperative.
        sentences = re.split(r"[.!?;\n]+", text)
        credential = any(re.search(r"\b(?:share|tell|send|disclose|provide)\b.{0,45}\b(?:otp|upi\s*pin|password)\b", sentence)
                         and not re.search(r"\b(?:never|do not|don't)\s+(?:ever\s+)?(?:share|tell|send|disclose|provide)\b", sentence)
                         for sentence in sentences)
    receive = bool(re.search(r"\b(?:receive|claim|get)\b.{0,40}\b(?:refund|cashback|reward)\b", text))
    debit = bool(re.search(r"\b(?:pay|approve|scan|send money)\b", text))
    remote = bool(re.search(r"\b(?:install|download|open)\b.{0,35}\b(?:anydesk|teamviewer|remote access|screen shar\w*)\b", text))
    return {"credential_request": credential, "refund_debit_conflict": receive and debit,
            "remote_access_request": remote}


def assess(intent: dict, history: dict, source: str = "signed_integration") -> dict:
    flags = language_flags(intent.get("message", ""))
    expected = intent.get("expected_payee")
    payee_mismatch = bool(expected and expected.casefold() != intent["payee"].casefold())
    amount_mismatch = bool(intent.get("expected_amount_paise") is not None
                           and intent["expected_amount_paise"] != intent["amount_paise"])
    paid = int(history.get("paid_count", 0))
    typical = history.get("median_paid_amount")
    ratio = intent["amount_paise"] / typical if typical and paid >= 5 else None
    device_known = history.get("device_known")
    new_device = None if intent.get("device_ref") is None else not bool(device_known)
    seen = bool(history.get("recipient_seen", False))
    signals = []

    def add(code, title, detail, severity, points):
        signals.append({"code": code, "title": title, "detail": detail, "severity": severity, "points": points})

    # Rule & Anomaly Checks
    if payee_mismatch:
        add("payee_changed", "Recipient does not match", "The payment UPI ID differs from the expected UPI ID supplied for this payment.", "critical", 95)
    if amount_mismatch:
        add("amount_changed", "Amount does not match", "The amount to be paid differs from the amount expected for this payment.", "critical", 90)
    if intent.get("purpose") == "receive_refund" or flags["refund_debit_conflict"]:
        add("refund_as_debit", "Receiving a refund presented as a payment", "This is a send-money instruction. Do not approve a debit simply to receive a refund.", "critical", 95)
    if flags["credential_request"]:
        add("credential_sharing", "Message asks you to share credentials", "The supplied message requests a PIN, OTP, or password. Do not disclose these to a person.", "critical", 90)
    if flags["remote_access_request"]:
        add("remote_access", "Remote-access instruction", "The supplied message asks for remote-access software in this payment conversation. Verify independently.", "high", 70)
    attempts = int(history.get("attempts_10m", 0))
    failures = int(history.get("failed_1h", 0))
    if attempts >= 5 and failures >= 3:
        add("attempt_burst", "Repeated attempts after failures", f"This integration recorded {attempts} earlier attempts in 10 minutes and {failures} failed outcomes in one hour.", "high", 75)
    elif attempts >= 5:
        add("attempt_velocity", "Unusual attempt frequency", f"This integration recorded {attempts} earlier attempts in 10 minutes. Frequency alone is not proof of fraud.", "medium", 45)
    if ratio is not None and ratio >= 4:
        add("amount_outlier", "Amount outside this customer's observed range", f"This amount is {ratio:.1f} times the median of {paid} earlier paid outcomes reported by this integration.", "medium", 50)

    # Real-Time Machine Learning Inference
    models = _get_ml_models()
    nlp_prob = None
    tx_prob = None
    msg_text = (intent.get("message") or "").strip()

    if models["message"] is not None and msg_text:
        try:
            nlp_prob = float(models["message"].predict_proba([msg_text])[0, 1])
            if nlp_prob >= 0.70 and not flags["credential_request"] and not flags["refund_debit_conflict"]:
                add("ml_scam_language", "AI Model: High Scam Probability in Payment Context",
                    f"The NLP Scam Classifier evaluated payment instructions at {nlp_prob*100:.1f}% scam probability.", "critical", 90)
            elif nlp_prob >= 0.40 and not flags["credential_request"]:
                add("ml_suspicious_language", "AI Model: Suspicious Language Patterns",
                    f"The NLP Scam Classifier detected deceptive phrasing patterns ({nlp_prob*100:.1f}% risk score).", "high", 60)
        except Exception:
            nlp_prob = None

    if models["transaction"] is not None:
        try:
            import pandas as pd
            from src.fraudshield.data import TRANSACTION_FEATURES
            row = {f: 0 for f in TRANSACTION_FEATURES}
            row["amount_inr"] = float(intent.get("amount_paise", 0)) / 100.0
            row["device_trust_score"] = 0.95 if (device_known and not new_device) else 0.45
            row["urgency_score"] = 9.0 if (flags["credential_request"] or flags["refund_debit_conflict"] or (nlp_prob and nlp_prob > 0.5)) else 1.0
            row["velocity_1h"] = float(attempts)
            row["receiver_known"] = bool(seen)
            row["new_device"] = bool(new_device) if new_device is not None else True
            row["asks_for_otp"] = bool(flags["credential_request"])
            row["asks_for_upi_pin"] = bool(flags["credential_request"])
            row["asks_to_scan_qr"] = bool(source == "user_supplied" and "qr" in intent.get("purpose", ""))
            row["asks_to_install_app"] = bool(flags["remote_access_request"])
            row["amount_match"] = not bool(amount_mismatch)
            row["merchant_match"] = not bool(payee_mismatch)
            row["message_present"] = bool(msg_text)
            df = pd.DataFrame([row])
            tx_prob = float(models["transaction"].predict_proba(df)[0, 1])
        except Exception:
            tx_prob = None

    ml_active = (models["message"] is not None or models["transaction"] is not None)
    if nlp_prob is not None and tx_prob is not None:
        combined_ml = max(nlp_prob, tx_prob, 0.6 * nlp_prob + 0.4 * tx_prob)
    elif nlp_prob is not None:
        combined_ml = nlp_prob
    elif tx_prob is not None:
        combined_ml = tx_prob
    else:
        combined_ml = 0.05

    ml_trust_score = round(max(0.0, min(100.0, (1.0 - combined_ml) * 100.0)), 1)

    missing = []
    if source != "signed_integration":
        missing.append("Details are user-supplied; there is no authenticated app connection for this check.")
    if not expected:
        missing.append("No independently supplied expected UPI ID is available.")
    if paid < 3:
        missing.append("Fewer than three earlier paid outcomes are available in this integration.")
    if not seen:
        missing.append("No earlier paid outcome to this recipient was found in this integration.")
    if new_device is None:
        missing.append("Device context is unavailable.")
    elif new_device:
        missing.append("This device reference has no earlier paid history in this integration.")

    score = min(100, max([0] + [signal["points"] for signal in signals]))
    if any(signal["severity"] == "critical" for signal in signals):
        decision, recommendation = "stop", "Do not approve this payment as presented. Resolve the specific mismatch or unsafe instruction first."
    elif signals or missing:
        decision, recommendation = "verify", "Pause and independently verify the recipient, amount, and request before deciding."
    else:
        decision, recommendation = "proceed", "No configured warning was found in the available integration history. Confirm the recipient and amount in your payment app."

    trust_index = max(0, min(100, 100 - score))
    trust_verdict = "TRUSTED" if decision == "proceed" else ("SUSPICIOUS" if decision == "verify" else "BLOCKED")

    return {
        "decision": decision,
        "trust_verdict": trust_verdict,
        "trust_index": trust_index,
        "risk_index": score,
        "recommendation": recommendation,
        "signals": signals,
        "missing_evidence": missing,
        "evidence": {
            "source": source,
            "previous_attempts_10m": attempts,
            "failed_outcomes_1h": failures,
            "paid_history_count": paid,
            "recipient_seen_in_paid_history": seen,
            "device_known_in_paid_history": None if new_device is None else not new_device,
            "amount_ratio": round(ratio, 3) if ratio is not None and math.isfinite(ratio) else None,
            "expected_payee_present": bool(expected)
        },
        "features": dict(zip(ML_FEATURES, [
            intent["amount_paise"], attempts, failures, paid, ratio,
            None if new_device is None else int(new_device), int(seen), int(payee_mismatch), int(amount_mismatch),
            int(flags["credential_request"]), int(flags["refund_debit_conflict"] or intent.get("purpose") == "receive_refund"),
            int(flags["remote_access_request"])
        ])),
        "engine_version": VERSION,
        "ml": {
            "mode": "active" if ml_active else "disabled",
            "model_name": "PaymentShield Multi-Modal Ensemble (Random Forest + TF-IDF NLP)",
            "fraud_probability": round(combined_ml, 4) if ml_active else 0.0,
            "trust_score": ml_trust_score if ml_active else 100.0,
            "nlp_scam_probability": round(nlp_prob, 4) if nlp_prob is not None else None,
            "behavioral_risk_score": round(tx_prob, 4) if tx_prob is not None else None,
            "verdict": "FRAUD SUSPECTED" if combined_ml >= 0.65 else ("VERIFY REQUIRED" if combined_ml >= 0.35 else "TRUSTED (SAFE)")
        },
        "disclaimer": "Risk guidance, not proof of fraud, a bank identity check, or payment authorization. A zero warning index can still require verification.",
    }


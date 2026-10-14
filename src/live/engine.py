"""Explain observed warning signals; never present a heuristic as confirmed fraud."""
from __future__ import annotations

import math
import re

VERSION = "paymentshield-live-1"
ML_FEATURES = ["amount_paise", "attempts_10m", "failed_1h", "paid_history_count",
               "amount_ratio", "new_device", "recipient_seen", "payee_mismatch",
               "amount_mismatch", "credential_request", "refund_debit_conflict", "remote_access_request"]


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
    return {
        "decision": decision, "risk_index": score,
        "recommendation": recommendation, "signals": signals, "missing_evidence": missing,
        "evidence": {"source": source, "previous_attempts_10m": attempts, "failed_outcomes_1h": failures,
                     "paid_history_count": paid, "recipient_seen_in_paid_history": seen,
                     "device_known_in_paid_history": None if new_device is None else not new_device,
                     "amount_ratio": round(ratio, 3) if ratio is not None and math.isfinite(ratio) else None,
                     "expected_payee_present": bool(expected)},
        "features": dict(zip(ML_FEATURES, [intent["amount_paise"], attempts, failures, paid, ratio,
            None if new_device is None else int(new_device), int(seen), int(payee_mismatch), int(amount_mismatch),
            int(flags["credential_request"]), int(flags["refund_debit_conflict"] or intent.get("purpose") == "receive_refund"),
            int(flags["remote_access_request"])])),
        "engine_version": VERSION,
        "ml": {"mode": "disabled", "reason": "The synthetic checkout model is not used for live decisions. Observed labelled data and prospective validation are required."},
        "disclaimer": "Risk guidance, not proof of fraud, a bank identity check, or payment authorization. A zero warning index can still require verification.",
    }

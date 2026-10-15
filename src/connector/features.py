"""One feature contract shared by training and online inference."""
from __future__ import annotations

import numpy as np

FEATURES = [
    "amount_inr", "amount_ratio", "account_age_days", "prior_payments",
    "attempts_1h", "failed_attempts_1h", "new_device", "hour",
    "session_age_seconds", "items_count", "customer_present",
]

CATALOG = {
    "headphones": {"name": "Studio wireless headphones", "amount_paise": 249900, "category": "Audio"},
    "keyboard": {"name": "Mechanical desk keyboard", "amount_paise": 599900, "category": "Workspace"},
    "camera": {"name": "Pocket travel camera", "amount_paise": 2499900, "category": "Photography"},
}

# These profiles are explicitly synthetic fixtures, never claims of bank telemetry.
DEMO_PROFILES = {
    "returning": {"account_age_days": 480, "prior_payments": 32, "typical_amount": 2800,
                  "attempts_1h": 0, "failed_attempts_1h": 0, "new_device": 0, "session_age_seconds": 240},
    "new_customer": {"account_age_days": 2, "prior_payments": 0, "typical_amount": None,
                     "attempts_1h": 0, "failed_attempts_1h": 0, "new_device": 1, "session_age_seconds": 80},
    "unusual": {"account_age_days": 340, "prior_payments": 20, "typical_amount": 400,
                "attempts_1h": 2, "failed_attempts_1h": 2, "new_device": 1, "session_age_seconds": 18},
    "burst": {"account_age_days": 1, "prior_payments": 0, "typical_amount": 1500,
              "attempts_1h": 12, "failed_attempts_1h": 8, "new_device": 1, "session_age_seconds": 6},
}


def build_features(amount_paise: int, quantity: int, profile: dict | None,
                   hour: int, attempts: int = 0, failures: int = 0) -> dict:
    amount = amount_paise / 100
    if profile is None:
        # An anonymous browser does not have a verified customer history.
        return dict(zip(FEATURES, [amount, 1.0, 0, 0, attempts, failures, 1, hour, 0, quantity, 0]))
    typical = profile.get("typical_amount")
    return dict(zip(FEATURES, [
        amount, min(100, amount / typical) if typical else 1.0,
        profile["account_age_days"], profile["prior_payments"],
        attempts + profile["attempts_1h"], failures + profile["failed_attempts_1h"],
        profile["new_device"], hour, profile["session_age_seconds"], quantity, 1,
    ]))


def rule_assessment(features: dict) -> tuple[int, list[str]]:
    score, reasons = 0, []
    if features["attempts_1h"] >= 8 and features["failed_attempts_1h"] >= 4:
        score = 90
        reasons.append("Repeated attempts with several recent failures.")
    if features["new_device"] and features["amount_ratio"] >= 4:
        score = max(score, 55)
        reasons.append("New session/device context and an amount above the example customer's usual range.")
    if not features["customer_present"]:
        score = max(score, 45)
        reasons.append("Verified customer history is unavailable; merchant review is required.")
    if features["new_device"] and features["account_age_days"] < 7:
        score = max(score, 45)
        reasons.append("New customer with no established device history.")
    return score, reasons


def engineered_features(frame):
    """Pre-payment transforms; no IDs, target labels, future outcomes, or split metadata."""
    result = frame[FEATURES].copy()
    for column in ["amount_inr", "amount_ratio", "account_age_days", "prior_payments", "session_age_seconds"]:
        result[column] = np.log1p(result[column].clip(lower=0))
    result["device_amount_interaction"] = frame["new_device"] * np.log1p(frame["amount_ratio"])
    result["new_account"] = (frame["account_age_days"] < 7).astype(int)
    result["short_session"] = (frame["session_age_seconds"] < 15).astype(int)
    result["new_device_overnight"] = frame["new_device"] * (frame["hour"] < 5).astype(int)
    return result

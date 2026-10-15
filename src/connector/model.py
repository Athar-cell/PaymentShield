from __future__ import annotations

import json
import math
import pickle
from pathlib import Path

import pandas as pd
from threadpoolctl import threadpool_limits
from .features import FEATURES, rule_assessment


class CheckoutRisk:
    """Load only locally generated/trusted model artifacts, never customer uploads."""
    def __init__(self, directory: Path):
        self.bundle = None
        self.report = None
        if (directory / "model.pkl").is_file() and (directory / "evaluation.json").is_file():
            with (directory / "model.pkl").open("rb") as handle:
                self.bundle = pickle.load(handle)
            self.report = json.loads((directory / "evaluation.json").read_text(encoding="utf-8"))

    @threadpool_limits.wrap(limits=1)
    def assess(self, features: dict) -> dict:
        rules, reasons = rule_assessment(features)
        model_score = None
        thresholds = {"review": 0.4, "block": 1.01}
        model_available = self.bundle is not None
        if model_available:
            try:
                probability = float(self.bundle["model"].predict_proba(pd.DataFrame([features])[FEATURES])[0, 1])
                if not math.isfinite(probability) or not 0 <= probability <= 1:
                    raise ValueError("Invalid model output")
                model_score = probability * 100
                thresholds = self.bundle["thresholds"]
            except Exception:
                model_available = False
        score = round(max(rules, model_score or 0), 1)
        if rules >= 90 or (model_available and model_score / 100 >= thresholds["block"]):
            decision = "stop"
        elif not model_available or not features["customer_present"] or rules >= 40 or model_score / 100 >= thresholds["review"]:
            decision = "review"
        else:
            decision = "proceed"
        if not model_available:
            reasons.append("The checkout model is unavailable; automatic approval is disabled.")
        elif model_score / 100 >= thresholds["review"]:
            reasons.append("The model flagged a combination resembling higher-risk synthetic checkout examples.")
        if not reasons:
            reasons.append("No configured warning threshold was crossed. This does not guarantee a safe payment.")
        return {"score": score, "decision": decision, "reasons": reasons,
                "model_score": round(model_score, 1) if model_score is not None else None,
                "rule_score": rules, "model_available": model_available,
                "model_version": self.bundle["version"] if model_available else None,
                "note": "Synthetic-data prototype risk index, not a real-world fraud probability."}

"""Reproducible checkout benchmark. All generated examples are SYNTHETIC.

Select a calibrated model and policy using validation only; open the test split
only after those choices. This is not evidence of production fraud accuracy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,
    f1_score, precision_recall_curve, precision_score, recall_score, roc_auc_score, roc_curve)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.connector.features import FEATURES, engineered_features, rule_assessment

VERSION = "checkout-v1"


def generate_data(users=1200, per_user=10, seed=20261015):
    rng = np.random.default_rng(seed)
    frames = []
    for index, name in enumerate(["train", "validation", "test"]):
        count = int(users * (0.6 if index == 0 else 0.2))
        n = count * per_user
        user = np.repeat(np.arange(count) + index * users, per_user)
        typical = np.repeat(rng.lognormal(7.5, 0.8, count), per_user)
        account = np.repeat(rng.integers(0, 1500, count), per_user)
        account[rng.random(n) < .15] = 2
        ratio = rng.lognormal(0, .9, n)
        # High-value genuine purchases and subtle suspicious attempts overlap.
        amount = np.clip(typical * ratio, 20, 150000)
        ratio = amount / typical
        attempts = rng.poisson(1.5, n)
        burst = rng.random(n) < .07
        attempts[burst] += rng.integers(4, 14, burst.sum())
        failures = rng.binomial(attempts, .30)
        new_device = rng.binomial(1, .32, n)
        hour = rng.integers(0, 24, n)
        prior = rng.poisson(12, n)
        prior[account < 7] = 0
        session = np.clip(rng.lognormal(4.8, 1.1, n), 2, 5000)
        # Unobserved noise + stochastic outcomes prevent labels being a simple rule copy.
        logit = (-4.6 + .8 * new_device + .55 * np.log1p(ratio)
                 + .25 * failures + .10 * attempts + .85 * (account < 7)
                 + .45 * ((hour < 5) & (new_device == 1)) + .65 * (session < 15)
                 + .9 * ((ratio > 4) & (new_device == 1)) + rng.normal(0, .7, n))
        labels = rng.binomial(1, 1 / (1 + np.exp(-logit)))
        frame = pd.DataFrame({
            "amount_inr": np.round(amount, 2), "amount_ratio": np.round(ratio, 4),
            "account_age_days": account, "prior_payments": prior, "attempts_1h": attempts,
            "failed_attempts_1h": failures, "new_device": new_device, "hour": hour,
            "session_age_seconds": session.round(), "items_count": rng.integers(1, 6, n),
            "customer_present": np.ones(n, dtype=int), "label_fraud": labels,
            "user_id": [f"synthetic_{x}" for x in user], "split": name,
            "source": "synthetic_stochastic_checkout_v1",
        })
        start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index * 40)
        frame["timestamp"] = [(start + timedelta(seconds=int(x))).isoformat() for x in np.linspace(0, 30 * 86400, n)]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def evaluate(y, p, threshold):
    y, p = np.asarray(y), np.asarray(p)
    predicted = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, predicted, labels=[0, 1]).ravel()
    precision, recall, _ = precision_recall_curve(y, p)
    fpr, tpr, _ = roc_curve(y, p)
    fraction, mean = calibration_curve(y, p, n_bins=8, strategy="quantile")
    def compact(*arrays):
        indices = np.unique(np.linspace(0, len(arrays[0]) - 1, min(100, len(arrays[0]))).astype(int))
        return [array[indices].tolist() for array in arrays]
    pr, rc = compact(precision, recall)
    fpr, tpr = compact(fpr, tpr)
    return {
        "rows": int(len(y)), "positive_rows": int(y.sum()), "threshold": float(threshold),
        "precision": float(precision_score(y, predicted, zero_division=0)),
        "recall": float(recall_score(y, predicted, zero_division=0)),
        "f1": float(f1_score(y, predicted, zero_division=0)),
        "average_precision": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)), "brier_score": float(brier_score_loss(y, p)),
        "false_positive_rate": float(fp / max(1, tn + fp)),
        "flag_rate": float(predicted.mean()), "missed_cases": int(fn), "false_alarms": int(fp),
        "confusion_matrix": [[int(tn), int(fp)], [int(fn), int(tp)]],
        "pr_curve": {"precision": pr, "recall": rc},
        "roc_curve": {"fpr": fpr, "tpr": tpr},
        "calibration": {"predicted": mean.tolist(), "observed": fraction.tolist()},
    }


def choose_thresholds(y, p):
    y, p = np.asarray(y), np.asarray(p)
    candidates = []
    for threshold in np.linspace(.01, .99, 99):
        pred = p >= threshold
        fp = int(((y == 0) & pred).sum())
        tp = int(((y == 1) & pred).sum())
        candidates.append({"threshold": float(threshold), "false_positive_rate": fp / max(1, int((y == 0).sum())),
                           "recall": tp / max(1, int(y.sum())), "precision": tp / max(1, int(pred.sum())),
                           "flagged": int(pred.sum())})
    feasible = [row for row in candidates if row["false_positive_rate"] <= .05]
    chosen = max(feasible, key=lambda row: (row["recall"], row["precision"], row["threshold"])) if feasible else {"threshold": 1.01}
    block = [row for row in candidates if row["threshold"] >= chosen["threshold"] and row["precision"] >= .90 and row["flagged"] >= 20]
    return {"review": chosen["threshold"], "block": min(row["threshold"] for row in block) if block else 1.01}, candidates


@threadpool_limits.wrap(limits=1)
def train(output: Path, users=1200):
    output.mkdir(parents=True, exist_ok=True)
    data = generate_data(users=users)
    parts = {name: data[data.split == name].copy() for name in ["train", "validation", "test"]}
    groups = {name: set(frame.user_id) for name, frame in parts.items()}
    assert not (groups["train"] & groups["test"] or groups["train"] & groups["validation"] or groups["test"] & groups["validation"])
    assert parts["train"].timestamp.max() < parts["validation"].timestamp.min() < parts["test"].timestamp.min()
    assert not set(FEATURES) & {"label_fraud", "user_id", "split", "source", "timestamp"}
    fitting, validation, test = (parts[key] for key in ["train", "validation", "test"])
    x, y = fitting[FEATURES], fitting.label_fraud
    folds = list(StratifiedGroupKFold(3, shuffle=True, random_state=42).split(x, y, groups=fitting.user_id))
    estimators = {
        "logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1500, random_state=42)),
        "engineered_logistic": make_pipeline(FunctionTransformer(engineered_features), StandardScaler(), LogisticRegression(max_iter=1500, random_state=42)),
        "random_forest": RandomForestClassifier(n_estimators=160, min_samples_leaf=12, max_depth=12, n_jobs=1, random_state=42),
        "gradient_boosting": HistGradientBoostingClassifier(max_iter=130, max_leaf_nodes=12, l2_regularization=5, random_state=42),
    }
    models, comparisons, curves = {}, {}, {}
    for name, estimator in estimators.items():
        model = CalibratedClassifierCV(estimator, method="sigmoid", cv=folds)
        model.fit(x, y)
        p = model.predict_proba(validation[FEATURES])[:, 1]
        thresholds, curve = choose_thresholds(validation.label_fraud, p)
        models[name] = (model, thresholds)
        comparisons[name] = evaluate(validation.label_fraud, p, thresholds["review"])
        curves[name] = curve
    winner = max(comparisons, key=lambda name: comparisons[name]["average_precision"])
    model, thresholds = models[winner]
    # A separate seed/window supplies the final lockbox after model/threshold selection.
    # The earlier seed's development test is deliberately not reported as blind evidence.
    lockbox = generate_data(users=users, seed=20261016)
    test = lockbox[lockbox.split == "test"].copy()
    test["user_id"] = "lockbox_" + test.user_id
    test["timestamp"] = (pd.to_datetime(test.timestamp) + pd.Timedelta(days=40)).astype(str)
    parts["test"] = test
    data = pd.concat([parts["train"], parts["validation"], test], ignore_index=True)
    probabilities = model.predict_proba(test[FEATURES])[:, 1]
    dummy = DummyClassifier(strategy="prior").fit(x, y)
    rules = np.array([rule_assessment(row)[0] / 100 for row in test[FEATURES].to_dict("records")])
    model_flags = probabilities >= thresholds["review"]
    rule_flags = rules >= .4
    policy_flags = model_flags | rule_flags
    slices = {}
    for name, mask in {"new_device": test.new_device == 1, "known_device": test.new_device == 0,
                       "large_amount": test.amount_inr >= 10000, "other_amount": test.amount_inr < 10000}.items():
        if mask.sum() and test.loc[mask, "label_fraud"].nunique() == 2:
            slices[name] = evaluate(test.loc[mask, "label_fraud"], probabilities[mask], thresholds["review"])
    report = {
        "version": VERSION, "source": "Entirely synthetic stochastic checkout benchmark",
        "selected_model": winner, "features": FEATURES, "thresholds": thresholds,
        "selection": "Highest validation average precision; sigmoid calibration uses train-only disjoint-user folds.",
        "threshold_policy": "Review: maximize validation recall under 5% FPR. Model-only stop: >=90% validation precision and >=20 flagged examples, otherwise disabled (1.01).",
        "data": {name: {"rows": len(frame), "users": frame.user_id.nunique(), "positives": int(frame.label_fraud.sum()),
                         "from": frame.timestamp.min(), "to": frame.timestamp.max()} for name, frame in parts.items()},
        "audit": {"user_overlap": 0, "chronological_windows": True, "demo_fixtures_in_training": False,
                  "label_fields_in_features": False, "training_seed": 20261015, "final_lockbox_seed": 20261016,
                  "development_holdout_replaced": True},
        "validation_comparison": comparisons, "validation_threshold_curve": curves[winner],
        "test": evaluate(test.label_fraud, probabilities, thresholds["review"]),
        "test_block_threshold": evaluate(test.label_fraud, probabilities, thresholds["block"]),
        "baselines": {"always_legitimate": evaluate(test.label_fraud, dummy.predict_proba(test[FEATURES])[:, 1], 1.01),
                      "rules_only": evaluate(test.label_fraud, rules, .4)},
        "combined_policy_test": evaluate(test.label_fraud, policy_flags.astype(float), .5),
        "slices": slices,
        "limitations": [
            "Synthetic outcomes are generated from invented assumptions, not observed fraud or chargebacks.",
            "Scores and calibration describe this simulator only; they do not establish real-world probabilities.",
            "Customer-history features in demo mode are fixtures; provider-test browsers have unknown history and require review.",
            "No bank-wide telemetry, personal Google Pay interception, identity verification or real-money processing.",
            "A stopped payment is not confirmed fraud; review adds friction and can miss fraud.",
            "The user/time-disjoint split measures new-customer generalization, not repeat-customer production performance.",
        ],
    }
    report["data"] = json.loads(json.dumps(report["data"], default=int))
    with (output / "model.pkl").open("wb") as handle:
        pickle.dump({"model": model, "thresholds": thresholds, "features": FEATURES, "version": VERSION}, handle)
    report["artifact_sha256"] = hashlib.sha256((output / "model.pkl").read_bytes()).hexdigest()
    (output / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    data.to_csv(output / "synthetic_checkout.csv", index=False)
    test.assign(model_probability=probabilities, model_flag=model_flags.astype(int),
                rule_flag=rule_flags.astype(int), combined_flag=policy_flags.astype(int)).to_csv(output / "test_predictions.csv", index=False)
    render_plots(report, output)
    return report


def render_plots(report, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    result = report["test"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 9), layout="constrained")
    cm = np.array(result["confusion_matrix"])
    axes[0, 0].imshow(cm, cmap="BuGn")
    axes[0, 0].set(xticks=[0, 1], yticks=[0, 1], xticklabels=["Proceed", "Flag"],
                   yticklabels=["Legitimate", "Suspicious"], xlabel="Model decision", ylabel="Synthetic true label",
                   title="Held-out model confusion matrix")
    for (i, j), value in np.ndenumerate(cm):
        axes[0, 0].text(j, i, str(value), ha="center", va="center", fontsize=18,
                        color="white" if value > cm.max() / 2 else "black")
    axes[0, 1].plot(result["pr_curve"]["recall"], result["pr_curve"]["precision"], color="#087f75")
    axes[0, 1].axhline(result["positive_rows"] / result["rows"], linestyle="--", color="gray", label="Prevalence baseline")
    axes[0, 1].set(xlabel="Recall", ylabel="Precision", title="Precision–recall", xlim=(0, 1), ylim=(0, 1))
    axes[0, 1].legend()
    axes[1, 0].plot(result["roc_curve"]["fpr"], result["roc_curve"]["tpr"], color="#087f75")
    axes[1, 0].plot([0, 1], [0, 1], "--", color="gray")
    axes[1, 0].set(xlabel="False-positive rate", ylabel="True-positive rate", title="ROC curve")
    axes[1, 1].plot(result["calibration"]["predicted"], result["calibration"]["observed"], "o-", color="#087f75")
    axes[1, 1].plot([0, 1], [0, 1], "--", color="gray")
    axes[1, 1].set(xlabel="Mean predicted score", ylabel="Observed synthetic positive share", title="Calibration — synthetic holdout")
    fig.suptitle(f"PaymentShield · {report['selected_model']} · SYNTHETIC DATA ONLY", fontsize=16)
    fig.savefig(output / "evaluation.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/checkout"))
    args = parser.parse_args()
    result = train(args.output)
    print(json.dumps({"model": result["selected_model"], "thresholds": result["thresholds"],
                      "test": {key: result["test"][key] for key in ["rows", "precision", "recall", "false_positive_rate", "average_precision", "confusion_matrix"]}}, indent=2))

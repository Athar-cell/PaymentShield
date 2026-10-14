"""Train an experimental model from authenticated live-event exports, not a generator.

Outcome labels are supplied by the integrator, NOT independently verified. Nothing
in this script enables model-driven payment approval in the running risk service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.live.engine import ML_FEATURES
from scripts.train_checkout import choose_thresholds, evaluate


def validate_export(payload):
    if payload.get("source") != "observed_app_events_with_integrator_labels":
        raise ValueError("Use an authenticated observed-event export. Synthetic checkout data is not accepted.")
    frame = pd.DataFrame(payload.get("rows", []))
    required = set(ML_FEATURES) | {"label_fraud", "timestamp", "assessment_id", "customer_group"}
    if not required <= set(frame):
        raise ValueError("Observed export is missing required feature, label, or audit fields.")
    if len(frame) < 500:
        raise ValueError("At least 500 adjudicated live events are required for this experimental benchmark.")
    if frame.assessment_id.isna().any() or frame.assessment_id.duplicated().any():
        raise ValueError("Assessment IDs must be unique and non-empty.")
    if frame.customer_group.isna().any():
        raise ValueError("Customer grouping metadata is missing.")
    if not frame.label_fraud.isin([0, 1]).all() or frame.label_fraud.value_counts().min() < 50 or frame.label_fraud.nunique() != 2:
        raise ValueError("Need at least 50 adjudicated examples of each binary label.")
    frame["timestamp"] = pd.to_numeric(frame.timestamp, errors="raise")
    if not np.isfinite(frame.timestamp).all():
        raise ValueError("Timestamps must be finite assessment-time values.")
    for name in ML_FEATURES:
        frame[name] = pd.to_numeric(frame[name], errors="raise")
        if np.isinf(frame[name].dropna()).any():
            raise ValueError("Infinite feature values are not valid observations.")
    frame = frame.sort_values(["timestamp", "assessment_id"]).reset_index(drop=True)
    # Do not split tied timestamps between windows, which would weaken the temporal audit.
    first, second = frame.timestamp.quantile(.6), frame.timestamp.quantile(.8)
    parts = {"train": frame[frame.timestamp <= first].copy(),
             "validation": frame[(frame.timestamp > first) & (frame.timestamp <= second)].copy(),
             "test": frame[frame.timestamp > second].copy()}
    for name, part in parts.items():
        if len(part) < 50 or part.label_fraud.nunique() != 2 or part.label_fraud.value_counts().min() < 10:
            raise ValueError(f"The chronological {name} window needs at least 50 rows and 10 examples of each label.")
    return parts


@threadpool_limits.wrap(limits=1)
def train_observed(payload, output):
    parts = validate_export(payload)
    training, validation, test = (parts[name] for name in ["train", "validation", "test"])
    x, y = training[ML_FEATURES], training.label_fraud.astype(int)
    folds = list(TimeSeriesSplit(n_splits=3).split(x))
    for fitting, calibration in folds:
        if y.iloc[fitting].nunique() < 2 or y.iloc[calibration].nunique() < 2:
            raise ValueError("Each train-only chronological calibration window must contain both labels.")
    candidates = {
        "logistic_regression": make_pipeline(SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
            StandardScaler(), LogisticRegression(max_iter=1500, random_state=42)),
        "random_forest": make_pipeline(SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
            RandomForestClassifier(n_estimators=120, max_depth=10, min_samples_leaf=10, n_jobs=1, random_state=42)),
        "gradient_boosting": make_pipeline(SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
            HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=12, l2_regularization=5, early_stopping=False, random_state=42)),
    }
    models, comparisons, threshold_choices = {}, {}, {}
    for name, candidate in candidates.items():
        calibrated = CalibratedClassifierCV(candidate, cv=folds, method="sigmoid", ensemble=True)
        calibrated.fit(x, y)
        predictions = calibrated.predict_proba(validation[ML_FEATURES])[:, 1]
        thresholds, _ = choose_thresholds(validation.label_fraud, predictions)
        models[name], threshold_choices[name] = calibrated, thresholds
        comparisons[name] = evaluate(validation.label_fraud, predictions, thresholds["review"])
    selected = max(comparisons, key=lambda name: comparisons[name]["average_precision"])
    model, threshold = models[selected], threshold_choices[selected]["review"]
    predictions = model.predict_proba(test[ML_FEATURES])[:, 1]
    result = evaluate(test.label_fraud, predictions, threshold)
    report = {
        "source": "observed_app_events_with_integrator_labels", "independently_verified": False,
        "enforcement": "disabled", "selected_model": selected, "features": ML_FEATURES,
        "threshold": threshold, "test": result, "validation_comparison": comparisons,
        "protocol": "Chronological train/validation/test windows; calibration uses earlier train-only time-series folds. Model and threshold selection use validation, not test.",
        "splits": {name: {"rows": len(part), "positives": int(part.label_fraud.sum()),
                           "from": float(part.timestamp.min()), "to": float(part.timestamp.max()),
                           "customers": int(part.customer_group.nunique())} for name, part in parts.items()},
        "customer_overlap_train_test": len(set(training.customer_group) & set(test.customer_group)),
        "limitations": [
            "Labels are claims by the integrating app; source authenticity and adjudication quality need independent review.",
            "Temporal windows can share returning customers. Identifiers are not model inputs; overlap is reported, not hidden.",
            "Exported features are snapshots recorded at assessment time. Future outcomes must never be reconstructed into them.",
            "Selection bias is likely when only reviewed or stopped transactions receive fraud labels.",
            "The 5% validation false-positive budget is a benchmark choice, not a guarantee on future traffic.",
            "Historical holdout results are not prospective validation or proof a model is safe to approve payments.",
            "No model trained here is automatically loaded into the live decision engine.",
        ],
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "model.pkl").open("wb") as handle:
        pickle.dump({"model": model, "features": ML_FEATURES, "threshold": threshold, "source": report["source"]}, handle)
    report["model_sha256"] = hashlib.sha256((output / "model.pkl").read_bytes()).hexdigest()
    (output / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    test[["assessment_id", "label_fraud"]].assign(model_score=predictions, prediction=(predictions >= threshold).astype(int)).to_csv(output / "test_predictions.csv", index=False)
    render_report(report, output)
    return report


def render_report(report, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    result = report["test"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), layout="constrained")
    matrix = np.array(result["confusion_matrix"])
    axes[0].imshow(matrix, cmap="BuGn")
    axes[0].set(xticks=[0, 1], yticks=[0, 1], xticklabels=["Legitimate", "Suspicious"],
                yticklabels=["Legitimate", "Suspicious"], xlabel="Prediction", ylabel="Integrator label", title="Temporal holdout confusion matrix")
    for (i, j), value in np.ndenumerate(matrix):
        axes[0].text(j, i, str(value), ha="center", va="center", color="white" if value > matrix.max()/2 else "black", fontsize=17)
    axes[1].plot(result["pr_curve"]["recall"], result["pr_curve"]["precision"], color="#137c65")
    axes[1].set(xlabel="Recall", ylabel="Precision", title="Precision-recall", xlim=(0, 1), ylim=(0, 1))
    axes[2].plot(result["roc_curve"]["fpr"], result["roc_curve"]["tpr"], color="#137c65")
    axes[2].plot([0, 1], [0, 1], "--", color="gray")
    axes[2].set(xlabel="False-positive rate", ylabel="True-positive rate", title="ROC")
    fig.suptitle("Observed app events / integrator labels / experimental evaluation only", fontsize=13)
    fig.savefig(output / "evaluation.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", default=Path("artifacts/observed"), type=Path)
    args = parser.parse_args()
    try:
        report = train_observed(json.loads(args.data.read_text(encoding="utf-8")), args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Observed-data training refused: {exc}\n")
    print(json.dumps({"selected_model": report["selected_model"], "test": report["test"]["confusion_matrix"], "enforcement": "disabled"}, indent=2))

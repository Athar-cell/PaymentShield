from __future__ import annotations

import json
import pickle
import re
from pathlib import Path

import pandas as pd
from sklearn.metrics import (accuracy_score, average_precision_score, confusion_matrix, f1_score,
                             precision_score, recall_score, precision_recall_curve)
from sklearn.model_selection import train_test_split

from .data import (TRANSACTION_FEATURES, clean_transaction_features, filter_realistic_rows,
                   make_message_pipeline, make_transaction_pipeline, read_dataset, split_rows)

MODEL_VERSION = "paymentshield-v2"


def _metrics(y_true, probabilities) -> dict:
    predictions = (probabilities >= 0.5).astype(int)
    precision, recall, _ = precision_recall_curve(y_true, probabilities)
    # Compact curves for a fast dashboard.
    indices = list(range(0, len(precision), max(1, len(precision) // 100)))
    return {
        "accuracy": round(float(accuracy_score(y_true, predictions)), 4),
        "precision": round(float(precision_score(y_true, predictions, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, predictions, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, predictions, zero_division=0)), 4),
        "pr_auc": round(float(average_precision_score(y_true, probabilities)), 4),
        "confusion_matrix": confusion_matrix(y_true, predictions, labels=[0, 1]).tolist(),
        "rows": int(len(y_true)), "positive_rows": int(y_true.sum()),
        "pr_curve": {"precision": [float(precision[i]) for i in indices],
                     "recall": [float(recall[i]) for i in indices]},
    }


def text_group(text: str) -> str:
    """Group repeated templates even when their rupee amounts change."""
    return re.sub(r"\s+", " ", re.sub(r"\d+(?:\.\d+)?", "<number>", str(text).casefold())).strip()


def message_group_splits(messages: pd.DataFrame) -> dict[str, pd.DataFrame]:
    frame = messages.copy()
    frame["_text_group"] = frame["message_text"].map(text_group)
    groups = frame.groupby("_text_group", as_index=False)["label_fraud"].first()
    train, other = train_test_split(groups, test_size=0.30, random_state=42, stratify=groups["label_fraud"])
    validation, test = train_test_split(other, test_size=0.5, random_state=42, stratify=other["label_fraud"])
    return {name: frame[frame["_text_group"].isin(part["_text_group"])].copy()
            for name, part in (("train", train), ("validation", validation), ("test", test))}


def audit_dataset(transactions: pd.DataFrame, messages: pd.DataFrame, qr_events: pd.DataFrame) -> dict:
    required = set(TRANSACTION_FEATURES) | {"label_fraud", "transaction_id", "user_id", "split"}
    missing = required - set(transactions.columns)
    if missing:
        raise ValueError(f"Incomplete transaction data: missing {sorted(missing)}")
    if transactions["transaction_id"].duplicated().any():
        raise ValueError("Duplicate transaction IDs found.")
    if transactions["label_fraud"].isna().any() or not transactions["label_fraud"].isin([0, 1]).all():
        raise ValueError("Invalid transaction labels; check for a truncated CSV upload.")
    if transactions.groupby("user_id")["split"].nunique().max() != 1:
        raise ValueError("A user appears in multiple splits.")
    for table in (messages, qr_events):
        if not table["transaction_id"].isin(transactions["transaction_id"]).all():
            raise ValueError("Linked data refers to a missing transaction.")
    return {"transactions": len(transactions), "messages": len(messages), "qr_events": len(qr_events),
            "users": int(transactions["user_id"].nunique()), "user_split_overlap": 0,
            "source": "synthetic prototype", "excluded_label_columns": ["label_fraud", "fraud_category", "message_category"]}


def train_models(data_dir: str | Path, artifact_dir: str | Path) -> dict:
    data_dir, artifact_dir = Path(data_dir), Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    transactions, messages, qr_events = read_dataset(data_dir)
    audit = audit_dataset(transactions, messages, qr_events)
    transactions = filter_realistic_rows(transactions)
    messages = filter_realistic_rows(messages)

    tx_train, tx_test = split_rows(transactions, "train"), split_rows(transactions, "test")
    tx_model = make_transaction_pipeline()
    tx_model.fit(clean_transaction_features(tx_train), tx_train["label_fraud"].astype(int))
    tx_probabilities = tx_model.predict_proba(clean_transaction_features(tx_test))[:, 1]
    tx_validation = split_rows(transactions, "validation")

    parts = message_group_splits(messages)
    msg_train, msg_test = parts["train"], parts["test"]
    msg_model = make_message_pipeline()
    msg_model.fit(msg_train["message_text"].fillna(""), msg_train["label_fraud"].astype(int))
    msg_probabilities = msg_model.predict_proba(msg_test["message_text"].fillna(""))[:, 1]

    feature_names = tx_model.named_steps["preprocess"].get_feature_names_out()
    importance = {}
    for feature, weight in zip(feature_names, tx_model.named_steps["model"].feature_importances_):
        original = feature.split("__", 1)[-1]
        base = next((f for f in sorted(TRANSACTION_FEATURES, key=len, reverse=True)
                     if original == f or original.startswith(f + "_")), original)
        importance[base] = importance.get(base, 0.0) + float(weight)

    metrics = {
        "model_version": MODEL_VERSION,
        "transaction_model": _metrics(tx_test["label_fraud"].astype(int), tx_probabilities),
        "message_model": _metrics(msg_test["label_fraud"].astype(int), msg_probabilities),
        "validation": {
            "transaction_model": _metrics(tx_validation["label_fraud"].astype(int),
                                         tx_model.predict_proba(clean_transaction_features(tx_validation))[:, 1]),
            "message_model": _metrics(parts["validation"]["label_fraud"].astype(int),
                                     msg_model.predict_proba(parts["validation"]["message_text"])[:, 1]),
        },
        "feature_importance": dict(sorted(importance.items(), key=lambda item: -item[1])),
        "data": {**audit, "transaction_train_rows": len(tx_train), "transaction_test_rows": len(tx_test),
                 "message_train_rows": len(msg_train), "message_test_rows": len(msg_test),
                 "message_template_groups": {k: int(v["_text_group"].nunique()) for k, v in parts.items()},
                 "transaction_split": "disjoint user IDs", "message_split": "disjoint normalized templates"},
        "limitations": [
            "All examples are synthetic. These metrics do not establish real-world fraud accuracy.",
            "Transaction feature ranges were generated with label-dependent patterns; scores may be unusually high.",
            "Message holdout separates repeated templates, but the template vocabulary is small and English-only.",
            "Risk indices combine heuristic rules and model output. They are not calibrated probabilities.",
        ],
    }
    for filename, model in (("transaction_model.pkl", tx_model), ("message_model.pkl", msg_model)):
        with (artifact_dir / filename).open("wb") as handle:
            pickle.dump(model, handle)
    (artifact_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics

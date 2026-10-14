from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)

from .data import (
    clean_transaction_features,
    filter_realistic_rows,
    make_message_pipeline,
    make_transaction_pipeline,
    read_dataset,
    split_rows,
)


def _metrics(y_true: pd.Series, probabilities: Any) -> dict[str, Any]:
    predictions = (probabilities >= 0.5).astype(int)
    matrix = confusion_matrix(y_true, predictions, labels=[0, 1])
    return {
        "accuracy": round(float(accuracy_score(y_true, predictions)), 4),
        "precision": round(float(precision_score(y_true, predictions, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, predictions, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, predictions, zero_division=0)), 4),
        "pr_auc": round(float(average_precision_score(y_true, probabilities)), 4),
        "confusion_matrix": matrix.tolist(),
        "rows": int(len(y_true)),
        "positive_rows": int(y_true.sum()),
    }


def train_models(data_dir: str | Path, artifact_dir: str | Path) -> dict[str, Any]:
    data_dir = Path(data_dir)
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    transactions, messages, _ = read_dataset(data_dir)
    transactions = filter_realistic_rows(transactions)
    messages = filter_realistic_rows(messages)

    tx_train = split_rows(transactions, "train")
    tx_test = split_rows(transactions, "test")
    tx_model = make_transaction_pipeline()
    tx_model.fit(clean_transaction_features(tx_train), tx_train["label_fraud"].astype(int))
    tx_probabilities = tx_model.predict_proba(clean_transaction_features(tx_test))[:, 1]

    msg_train = split_rows(messages, "train")
    msg_test = split_rows(messages, "test")
    msg_model = make_message_pipeline()
    msg_model.fit(msg_train["message_text"].fillna(""), msg_train["label_fraud"].astype(int))
    msg_probabilities = msg_model.predict_proba(msg_test["message_text"].fillna(""))[:, 1]

    with (artifact_dir / "transaction_model.pkl").open("wb") as handle:
        pickle.dump(tx_model, handle)
    with (artifact_dir / "message_model.pkl").open("wb") as handle:
        pickle.dump(msg_model, handle)
    metrics = {
        "transaction_model": _metrics(tx_test["label_fraud"].astype(int), tx_probabilities),
        "message_model": _metrics(msg_test["label_fraud"].astype(int), msg_probabilities),
        "data": {
            "transaction_train_rows": int(len(tx_train)),
            "transaction_test_rows": int(len(tx_test)),
            "message_train_rows": int(len(msg_train)),
            "message_test_rows": int(len(msg_test)),
            "split_strategy": "user-level split supplied in the dataset",
        },
    }
    (artifact_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


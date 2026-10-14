from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.ensemble import RandomForestClassifier


TRANSACTION_NUMERIC_FEATURES = [
    "amount_inr",
    "hour",
    "receiver_age_days",
    "device_trust_score",
    "velocity_1h",
    "account_age_days",
    "prior_txn_count",
    "urgency_score",
]

TRANSACTION_BOOLEAN_FEATURES = [
    "receiver_known",
    "merchant_match",
    "new_device",
    "location_changed",
    "asks_for_otp",
    "asks_for_upi_pin",
    "asks_to_scan_qr",
    "asks_to_install_app",
    "qr_scanned",
    "qr_merchant_match",
    "amount_match",
]

TRANSACTION_CATEGORICAL_FEATURES = [
    "city",
    "state",
    "upi_app",
    "device_type",
    "payment_type",
    "channel",
    "day_of_week",
    "message_present",
    "message_category",
]

TRANSACTION_FEATURES = (
    TRANSACTION_NUMERIC_FEATURES
    + TRANSACTION_BOOLEAN_FEATURES
    + TRANSACTION_CATEGORICAL_FEATURES
)


def read_dataset(data_dir: str | Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load the three linked tables from the repository data directory."""
    data_dir = Path(data_dir)
    transactions = pd.read_csv(data_dir / "fraudshield_upi_transactions.csv")
    messages = pd.read_csv(data_dir / "fraudshield_scam_messages.csv")
    qr_events = pd.read_csv(data_dir / "fraudshield_qr_events.csv")
    return transactions, messages, qr_events


def clean_transaction_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Prepare only model-safe features; labels and identifiers are excluded."""
    missing = [column for column in TRANSACTION_FEATURES if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing transaction features: {missing}")

    clean = frame[TRANSACTION_FEATURES].copy()
    for column in TRANSACTION_BOOLEAN_FEATURES:
        clean[column] = (
            clean[column]
            .fillna(False)
            .astype(str)
            .str.lower()
            .map({"true": 1, "false": 0, "1": 1, "0": 0})
            .fillna(0)
            .astype(int)
        )
    for column in TRANSACTION_CATEGORICAL_FEATURES:
        clean[column] = clean[column].fillna("unknown").astype(str)
    for column in TRANSACTION_NUMERIC_FEATURES:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    return clean


def make_transaction_pipeline() -> Pipeline:
    numeric = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    preprocess = ColumnTransformer(
        transformers=[
            ("numeric", numeric, TRANSACTION_NUMERIC_FEATURES),
            ("boolean", "passthrough", TRANSACTION_BOOLEAN_FEATURES),
            ("categorical", categorical, TRANSACTION_CATEGORICAL_FEATURES),
        ]
    )
    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=14,
        min_samples_leaf=2,
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=-1,
    )
    return Pipeline([("preprocess", preprocess), ("model", model)])


def make_message_pipeline() -> Pipeline:
    return Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=2,
                    max_features=12_000,
                    sublinear_tf=True,
                ),
            ),
            (
                "model",
                LogisticRegression(
                    max_iter=1200,
                    class_weight="balanced",
                    random_state=42,
                ),
            ),
        ]
    )


def filter_realistic_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Exclude hand-authored demo rows from model fitting and evaluation."""
    if "source_type" not in frame.columns:
        return frame
    return frame[frame["source_type"] != "synthetic_demo"].copy()


def split_rows(frame: pd.DataFrame, split: str) -> pd.DataFrame:
    return frame[frame["split"].astype(str).eq(split)].copy()


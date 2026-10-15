from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fraudshield.training import train_models


def main() -> None:
    parser = argparse.ArgumentParser(description="Train FraudShield transaction and message models.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--artifact-dir", default="artifacts")
    args = parser.parse_args()
    metrics = train_models(Path(args.data_dir), Path(args.artifact_dir))
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()

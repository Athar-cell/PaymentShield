# PaymentShield

PaymentShield is a hackathon MVP for FraudShield AI: pre-transaction UPI fraud screening with explainable signals.

## What is implemented

- Behavioral transaction model using a leakage-safe user-level split.
- Scam-message classifier using TF-IDF and logistic regression.
- QR and payment rules for collect-request abuse, PIN/OTP requests, remote-access scams, and merchant mismatch.
- Explainable risk engine that combines model probabilities with deterministic safety rules.
- Streamlit demo for the four showcase cases: fake refund, fake KYC, QR mismatch, and a normal payment.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/train_models.py
pytest
streamlit run app.py
```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1`.

## Dataset

The synthetic, privacy-safe dataset is in `data/`:

- `fraudshield_upi_transactions.csv`
- `fraudshield_scam_messages.csv`
- `fraudshield_qr_events.csv`
- `fraudshield_data_dictionary.csv`

The transaction and message training scripts exclude hand-authored demo rows. The supplied train/validation/test split is group-safe by `user_id`, so one user does not appear in multiple splits.

## Evaluation

The training script writes `artifacts/metrics.json` with accuracy, precision, recall, F1, PR-AUC, and confusion matrices. For the hackathon presentation, emphasize fraud recall and PR-AUC, then show the four demo scenarios with explanations.

## Safety and limitations

This is a synthetic prototype. It must not be used to approve or decline real payments. Production use would require validated bank data, calibration, drift monitoring, privacy review, human escalation, and adversarial testing.


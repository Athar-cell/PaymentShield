# PaymentShield

An explainable UPI payment screening demo for the hackathon: payment context, message analysis, and QR inspection in one Streamlit application.

## The experience

- **Overview:** product introduction, dataset totals, and benchmark activity.
- **Scan & explain:** payment form, four working scenarios, risk gauge, warning evidence, component charts, and a downloadable explanation report.
- **QR inspector:** upload a PNG/JPEG/WebP or paste a UPI URI. Decode the payee and amount locally, check merchant/amount mismatches, and reject ambiguous payloads. No link is opened and no payment is initiated.
- **Risk intelligence:** interactive filters, scam categories, payment-type comparisons, amount distributions, hourly activity, correlation heatmap, and filtered CSV download.
- **Model lab:** real held-out metrics from the current training run, confusion matrix, precision-recall curve, feature importance, and evaluation limitations.
- **Demo studio:** refund trap, KYC impersonation, replaced QR, and a normal lunch payment with explanations.

## Run locally

Use Python 3.11 or 3.12 from the repository root:

~~~bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
streamlit run app.py
~~~

Models train automatically when a model-powered page opens for the first time. Training is cached and versioned. To train explicitly:

~~~bash
python scripts/train_models.py --data-dir data --artifact-dir artifacts
~~~

Generated model files stay local. Load only trusted artifacts produced by this repository.

## Deploy to Streamlit Community Cloud

Connect the GitHub repository, select branch main and entrypoint app.py, and use Python 3.11 or 3.12. The requirements file installs the dependencies. First use of Scan & explain, Model lab, or Demo studio trains the small models. No API keys or bank credentials are needed.

## Models and explanations

The transaction model uses a Random Forest with preprocessing fitted only on the training split. The message model uses TF-IDF and logistic regression. Observable rules explain explicit requests for PINs/OTPs, collect requests disguised as refunds, remote-access requests, and QR mismatches.

The risk index blends available model outputs and rules. Explicit warning floors prevent a strong warning from being averaged away by weak model output. It is a heuristic index, not a calibrated real-world fraud probability. Rule evidence is distinguished from global model feature importance; the app does not claim SHAP explanations.

## Data and evaluation

The complete linked synthetic dataset contains:

| Table | Rows |
| --- | ---: |
| Transactions | 6,004 |
| Messages | 2,534 |
| QR events | 1,451 |

Hand-authored demo rows are excluded from model training and evaluation. Transaction splits are disjoint by user ID. Message evaluation uses disjoint normalized templates (amounts are normalized), rather than reporting performance on repeated sentences. Labels, fraud_category, message_category, source metadata, and identifiers are excluded from model inputs.

Transaction records are stored as eleven readable CSV parts in data/transactions/. The loader combines them automatically. This replaces the earlier incomplete single-file upload. To export one complete CSV, use the download option in Risk intelligence with all filters selected. The integrity tests check the combined row count and linked identifiers.

Tests verify row counts, linked keys, user/template separation, critical rule behavior, QR decoding/validation, page rendering, form scenario switching, and empty chart filters. GitHub Actions runs the suite, trains the models, and retains a metrics report.

## Scope and limitations

All examples are synthetic and English-only. Their distributions reflect generator assumptions; high scores on these examples do not establish production detection accuracy. The QR payee name is self-declared, and a name mismatch is a reason to verify, not proof of fraud. A missing QR amount is treated as unknown rather than a mismatch. Device trust and historical context are demo inputs, not telemetry measured from the visitor's browser.

The app screens examples; it does not connect to a bank or approve/decline payments. Session history is held in Streamlit session state and is not written to a database. It omits message text. Uploaded QR bytes are decoded in memory; model training writes only generated models and aggregate metrics.

## Three-minute demo

1. Overview: explain the problem and the three analysis channels.
2. Scan & explain: run Refund trap and show why a collect request contradicts a refund promise.
3. Compare Lunch with a friend to demonstrate a low-risk result.
4. QR inspector: download/upload the placeholder demo QR and change the expected merchant.
5. Risk intelligence: filter a payment type and explain the benchmark patterns.
6. Model lab: show measured results, the split design, and the synthetic-data limitations.

See data/README.md for the source guidance and dataset provenance.

# PaymentShield

## New direction: checkout risk connector

The project now includes **our own checkout, pre-payment risk decisions, merchant review,
and a replaceable payment-provider adapter**. This is more than a dashboard: the backend
prevents blocked/review payments from reaching provider checkout.

The original Streamlit research application is preserved below as the **legacy research
guide**. Its data, model metrics, commands and deployment instructions are separate from
the new checkout connector. Do not present the legacy UPI benchmark as checkout accuracy.

### Run the new checkout

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-connector.txt
.\.venv\Scripts\python.exe scripts/train_checkout.py
$env:PAYMENTSHIELD_MERCHANT_TOKEN = "choose-a-long-random-local-value"
.\.venv\Scripts\python.exe -m uvicorn src.connector.api:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. The four sections are Checkout playground, Model evaluation,
Merchant review, and Integration guide. The default is clearly labelled demo mode: no
payment app opens and no money moves. Browser testing uses a separate disposable database.

### What is ours, and why an external API may still be needed

PaymentShield owns the interface, risk model, decision rules, review workflow, order store,
checkout gating, and event trail. A payment adapter is a replaceable connection to the
infrastructure that executes and confirms a payment. It is not the PaymentShield product.

You do **not** need a Razorpay account to build or run our product in demo mode. The included
optional Razorpay adapter needs test keys only if you choose to exercise Razorpay's external
checkout/API. It rejects live keys. No account has been connected merely by adding this code.

A personal Google Pay account is not a general-purpose API that lets this app intercept
every transaction inside Google Pay. A real merchant handoff requires an eligible supported
integration. Provider test mode is not a live Google Pay debit demonstration. Do not promise
that a generic UPI link proves payment success.

### End-to-end flow

1. Server prices a catalog order; browser-supplied amounts are rejected.
2. Risk engine returns proceed, review, or stop with explanations.
3. Review orders require an authenticated merchant action and a recorded note. Hard-blocked
   orders cannot be overridden in this prototype.
4. Only ready orders can create a provider order. Repeated requests reuse the existing one.
5. Demo mode simulates bank outcomes. The optional test adapter verifies provider signatures,
   amount, currency, order association, and payment status server-side.
6. Only captured provider payments become paid. Authorized is not paid. Signed webhook
   processing suppresses duplicate bodies and does not let late failure events undo paid.

### Checkout model and evaluation

`scripts/train_checkout.py` generates a reproducible **12,000-row synthetic stochastic
checkout benchmark**, comparing raw logistic regression, feature-engineered logistic
regression, Random Forest, and gradient boosting. A dummy baseline and rules-only policy
are evaluated separately. No legacy demo scenario rows are used in checkout training.

Training and validation contain disjoint users in ordered date windows. Calibration uses
train-only disjoint-user folds. Model selection and thresholds use validation only. A separate
seed and later date window supply the final 2,400-row lockbox after selection; an earlier
development holdout is not presented as blind evidence. Threshold selection targets at most
5% validation false-positive rate; the test rate is reported honestly and can be higher.

The model-only stop threshold is disabled unless validation has at least 90% precision and
20 flagged examples. This is not a statistical guarantee. Explicit burst/failure rules can
still stop a simulated order. Missing/broken models and unknown customer history go to review.

The Model evaluation page shows an actual confusion matrix, precision–recall, ROC, calibration,
validation model comparison, and combined-policy comparison. Output files are generated in
`artifacts/checkout/`: `evaluation.json`, `evaluation.png`, `test_predictions.csv`, the generated
dataset, and a trusted local model pickle. Models, databases, secrets, and generated checkout
artifacts are not committed. Run the script to reproduce them or download CI artifacts.

**The model is not production-ready.** The benchmark deliberately contains overlapping cases
and stochastic labels. Synthetic assumptions are not observed fraud. Inspect missed cases and
false alarms instead of claiming perfect accuracy; real, consented and appropriately labelled
merchant data is still required for meaningful deployment validation.

### Security and scope

- Session-bound order access, server pricing, strict input validation, same-origin browser
  writes, merchant-token review, order expiry and idempotency are implemented.
- Test-provider mode ignores browser-selected customer fixtures. With no authenticated
  customer-history integration, these orders require merchant review.
- No card numbers, CVVs, UPI PINs, or OTPs are collected by PaymentShield.
- Secret values stay in server environment variables. `.env.example` documents them but is
  not automatically loaded. Never paste actual secrets into the repository or a chat.
- A provider timeout yields an uncertain state, not a blind retry. Automated reconciliation,
  multi-merchant isolation, durable production hosting, customer identity, distributed rate
  limits, and cross-site SDK support remain future work.
- This connector uses FastAPI + plain HTML/CSS/JavaScript and SQLite for a small single-service
  prototype. It is not the previously proposed Next.js/PostgreSQL architecture and is not
  deployed by the old Streamlit Community Cloud instructions.

See [the full connector guide](docs/CONNECTOR.md), [the API implementation](src/connector/api.py),
[the same-origin checkout client](web/connector-sdk.js), and [the training script](scripts/train_checkout.py).

---

## Legacy Streamlit research guide

**Understand a suspicious payment before you approve it.**

[![PaymentShield CI](https://github.com/Athar-cell/PaymentShield/actions/workflows/ci.yml/badge.svg)](https://github.com/Athar-cell/PaymentShield/actions/workflows/ci.yml)

PaymentShield is a Python and Streamlit hackathon prototype for explainable UPI payment screening. It brings together transaction context, scam-message classification, and QR inspection, then shows a risk index, the warning signals, and a recommendation.

The project was initially discussed as **FraudShield AI**. **PaymentShield** is the current application and repository name.

> This is a research/demo application using synthetic data. It does not connect to a bank, verify a real account, initiate payments, or guarantee that a payment is safe. A score of 85/100 is a prototype risk index, not an 85% probability of fraud.

## Contents

- [Project in plain English](#project-in-plain-english)
- [What is implemented](#what-is-implemented)
- [Quick start](#quick-start)
- [Using the six pages](#using-the-six-pages)
- [Architecture and request flow](#architecture-and-request-flow)
- [Repository map](#repository-map)
- [Dataset and provenance](#dataset-and-provenance)
- [Dataset schema](#dataset-schema)
- [Training and evaluation](#training-and-evaluation)
- [How the risk index works](#how-the-risk-index-works)
- [How QR inspection works](#how-qr-inspection-works)
- [Graphs and how to read them](#graphs-and-how-to-read-them)
- [Results and verified checks](#results-and-verified-checks)
- [Deploy to Streamlit Community Cloud](#deploy-to-streamlit-community-cloud)
- [Privacy and runtime behavior](#privacy-and-runtime-behavior)
- [Troubleshooting](#troubleshooting)
- [Hackathon presentation guide](#hackathon-presentation-guide)
- [Next development steps](#next-development-steps)
- [Frequently asked questions](#frequently-asked-questions)
- [References](#references)

## Project in plain English

A message can promise a refund while the payment request actually asks the user to send money. A QR can display a different payee from the shop the user intended to pay. A supposed support agent can ask for a security code or remote access.

A transaction classifier alone cannot explain all of those situations. PaymentShield examines three kinds of evidence:

1. **Payment context:** amount, receiver familiarity, device context, activity, and payment type.
2. **Message content:** the wording of the payment conversation and explicit requests for credentials or remote access.
3. **QR details:** the encoded payee, merchant name, and amount compared with the user's expectations.

For example, choose **Refund trap** and enter a ₹5,000 collect request accompanied by “Approve the collect request to receive your refund.” The explicit rule sets a minimum index of **85/100** because the promised credit conflicts with the requested debit. The interface explains that conflict rather than displaying only a class label.

The intended benefit is a clear pause-and-verify decision aid. The current implementation demonstrates that workflow; it is not a deployed banking control.

## What is implemented

| Capability | Current implementation |
| --- | --- |
| Web application | Six Streamlit pages with desktop and mobile layouts |
| Transaction model | Random Forest classifier with a fitted preprocessing pipeline |
| Message model | TF-IDF features and logistic regression |
| Explicit warning rules | Refund/collect contradiction, PIN and OTP requests, app/remote-access requests, QR mismatches, and contextual flags |
| QR scanner | Image decoding with OpenCV plus UPI URI parsing |
| Explanations | Observable rule evidence, model component outputs, risk band, and suggested verification steps |
| Analytics | Interactive Plotly charts, dataset filters, and CSV export |
| Model evaluation | Held-out metrics, confusion matrices, precision–recall curves, and global feature importance |
| Demo scenarios | Refund trap, KYC impersonation, replaced QR, and a normal lunch payment |
| Downloads | Explanation JSON, filtered transaction CSV, and a sample QR |
| Validation | Automated data/rule/UI tests, model training, and browser screenshot checks |

**Not implemented:** bank or NPCI integration, a real-time payment feed, bank-verified merchant lookup, automatic device telemetry, user accounts, a persistent incident database, a FastAPI service, an LLM/chatbot, multilingual detection, or production probability calibration.

The absence of an LLM is deliberate in this version: the message classifier is a conventional machine-learning pipeline and the explanations are generated from explicit rules. No paid AI API key is required.

## Quick start

### Prerequisites

- Git.
- Python **3.11** for the closest match to the verified CI environment. Python 3.12 can also be used, but keep development and deployment versions consistent.
- Internet access to install dependencies. Once installed, the local screening workflow does not need an external inference API.

Run commands from the repository root.

### Windows PowerShell

These commands use the virtual environment's interpreter directly, so activation is optional.

~~~powershell
git clone https://github.com/Athar-cell/PaymentShield.git
cd PaymentShield

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m streamlit run app.py
~~~

### macOS or Linux

~~~bash
git clone https://github.com/Athar-cell/PaymentShield.git
cd PaymentShield

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python -m pytest -q
.venv/bin/python -m streamlit run app.py
~~~

Open the local URL printed by Streamlit, normally `http://localhost:8501`.

### Train explicitly

Opening **Scan & explain**, **Model lab**, or **Demo studio** prepares the models automatically when needed. To run training yourself:

~~~powershell
# Windows
.\.venv\Scripts\python.exe scripts/train_models.py --data-dir data --artifact-dir artifacts
~~~

~~~bash
# macOS/Linux
.venv/bin/python scripts/train_models.py --data-dir data --artifact-dir artifacts
~~~

Training writes:

- `artifacts/transaction_model.pkl`
- `artifacts/message_model.pkl`
- `artifacts/metrics.json`

These are generated files, not committed model binaries. The application checks a model-version marker and a data-file fingerprint consisting of file names, sizes, and modification times. This fingerprint is a refresh mechanism, not a cryptographic integrity proof.

## Using the six pages

### 1. Overview

Start here when introducing the project. It explains the three analysis channels, shows the dataset totals, and plots daily activity in the synthetic benchmark.

**Check a payment** opens the analysis form. **Run the refund demo** selects the refund example. **Open demo studio** opens the four-case showcase.

### 2. Scan & explain

1. Choose a scenario or edit its values.
2. Enter the amount and payment type.
3. Set whether the receiver is known and whether the device/location changed.
4. Paste the message and compare the expected merchant with the payee name.
5. Optionally expand **More transaction context** for hour, payment velocity, and receiver history.
6. Click **Analyze payment**.

The result includes a risk gauge, risk band, recommendation, explanation list, rule-evidence table, component chart, and JSON download. After more than one analysis, a compact session history is available.

**Important input distinction:** device trust and history are manually supplied demo context. They are not measured from the visitor's phone. The adapter also supplies fixed context for fields not exposed in the form, including Bengaluru/Karnataka, Google Pay, an Android device, an account age of 500 days, 30 prior transactions, and Monday. These are assumptions for the prototype, not verified customer attributes.

The payee-name field is manual on this page. Use **QR inspector** to decode an actual image.

### 3. QR inspector

Upload an image or paste a UPI URI, enter the merchant you expected and the intended amount, then click **Inspect QR**.

This page intentionally uses **rules only**. A QR image does not establish device history, receiver familiarity, or message context, so it is not presented as a full behavioral-model assessment.

The page displays decoded fields, name/amount comparisons, and an explanation. The sample QR uses `demo@example` as a placeholder; it is for inspection tests, not payment.

### 4. Risk intelligence

Filter by payment type and dataset split. Every chart uses the selected transaction rows.

The preview table shows at most 200 rows. The download contains the **entire filtered selection**. To obtain one combined transaction CSV, choose every payment type and the **All** split, then download it.

### 5. Model lab

Choose the transaction or message model to inspect precision, recall, F1, the metric stored as `pr_auc`, the confusion matrix, and the precision–recall curve.

The page also shows global Random Forest feature importance, the evaluation design, and limitations. These charts are computed from model training; they are not decorative accuracy numbers.

### 6. Demo studio

Compare all four scenarios with their explanations. **Explore this example** opens that scenario in the analysis form so a judge can change the inputs.

Expected showcase behavior is three high-risk examples and one low-risk example. Exact model components can change if training data or dependency versions change.

## Architecture and request flow

The application runs as one Streamlit Python service. There is no separate frontend server or HTTP prediction API in this repository.

~~~mermaid
flowchart TD
    User[User opens PaymentShield] --> UI[Streamlit application]
    UI --> Form[Payment context and message]
    UI --> QR[QR image or UPI URI]
    UI --> Dashboard[Dataset charts and Model lab]

    Form --> Adapter[Prepare inputs and derive text flags]
    Adapter --> Transaction[Random Forest transaction pipeline]
    Adapter --> Message[TF-IDF and logistic regression]
    Adapter --> Rules[Observable warning rules]

    QR --> Decode[Decode and validate payload]
    Decode --> Compare[Compare payee name and amount]
    Compare --> Rules

    Transaction --> Engine[Weighted risk index and warning floors]
    Message --> Engine
    Rules --> Engine
    Engine --> Result[Risk band, evidence, recommendation and report]
~~~

The diagram shows shared components; the QR inspector sends its result through the rules-only path. It does not run the transaction/message models.

For a normal analysis request:

1. `scenario_features()` combines form values, defaults, and derived message flags.
2. `clean_transaction_features()` prepares the model's feature columns.
3. Available models produce class-1 outputs.
4. Explicit rules collect warning signals.
5. `analyze_payment()` combines available components and applies any minimum warning index.
6. The app renders the result and can export a report.

The dashboard reads the benchmark tables. It is not a stream of live customer payments.

## Repository map

~~~text
PaymentShield/
├── app.py                         # All six Streamlit pages and runtime wiring
├── requirements.txt               # Runtime and test dependencies
├── pytest.ini                     # Test path/import configuration
├── .gitignore                     # Excludes generated models and caches
├── .streamlit/
│   └── config.toml                # Streamlit theme and server configuration
├── .github/workflows/
│   └── ci.yml                     # Tests, training, browser checks and artifacts
├── data/
│   ├── README.md                  # Data provenance and limitations
│   ├── transactions/
│   │   ├── part-001.csv
│   │   ├── ...
│   │   └── part-011.csv
│   ├── fraudshield_scam_messages.csv
│   ├── fraudshield_qr_events.csv
│   └── fraudshield_data_dictionary.csv
├── scripts/
│   ├── train_models.py            # Training command
│   └── capture_demo.py            # Browser smoke check and screenshots for CI
├── src/
│   └── fraudshield/
│       ├── data.py                # Loading, preprocessing and model constructors
│       ├── training.py            # Audits, splits, fitting, metrics and artifacts
│       ├── risk_engine.py         # Text flags, warning rules and score combination
│       ├── qr.py                  # QR decoding, URI validation and name comparison
│       ├── scenarios.py           # Four showcase cases and input adapter
│       └── ui.py                  # Theme, chart styling and gauge helper
├── tests/
│   ├── test_risk_engine.py
│   ├── test_upgrade.py
│   └── test_app.py
└── artifacts/                     # Generated when training runs
    ├── transaction_model.pkl
    ├── message_model.pkl
    └── metrics.json
~~~

Core dependencies are pandas/NumPy for data, scikit-learn for ML, Streamlit for the app, Plotly for charts, OpenCV/Pillow for image inspection, qrcode for the sample image, and pytest for tests.

Playwright is installed separately by the CI workflow for real-browser checks. It is not needed just to run the app.

## Dataset and provenance

### What the data actually contains

| Table | Total rows | Purpose |
| --- | ---: | --- |
| Transactions | 6,004 | Synthetic payment context and labels |
| Messages | 2,534 | Synthetic English messages linked to transactions |
| QR events | 1,451 | Synthetic merchant/payee comparison records |
| Distinct transaction users | 899 | Synthetic IDs used for user-separated evaluation |

The transaction table contains **941 fraud labels** and **5,063 legitimate labels**, including the four hand-authored demo rows. Its approximately 15.7% fraud-label share is a generator choice, not real UPI fraud prevalence.

These are **all synthetic examples**. No Kaggle/IEEE-CIS or real bank transaction dataset is currently bundled. The awareness sources informed scam patterns; they did not supply the labeled transaction records.

Covered positive categories are:

`refund_scam`, `kyc_scam`, `qr_scam`, `lottery_scam`, `remote_access_scam`, `otp_scam`, `impersonation_scam`, `investment_scam`, and `support_scam`.

The negative category is `legitimate`.

### Why transaction data is split into parts

Earlier single-file GitHub uploads were incomplete. The current version restores the complete transactions as eleven readable CSV parts: ten parts with 600 rows each and one with four rows. Each part includes the same header.

`read_dataset()` sorts and concatenates these parts automatically. A legacy single-file path is supported only when no parts are present. Do not load just one part and treat it as the full benchmark.

The original generator is not yet checked into this repository. Training is repeatable from the included records and fixed random seeds, but this is not yet a complete raw-data regeneration pipeline.

See [data/README.md](data/README.md) for provenance and [the column dictionary](data/fraudshield_data_dictionary.csv) for additional definitions.

## Dataset schema

`label_fraud` is the supervised target: **1 = synthetic fraud/suspicious scenario, 0 = synthetic legitimate scenario**. These are assigned labels, not independently investigated real incidents.

### Transaction model inputs

All 27 input columns are listed below. The numeric/categorical distinction describes the current preprocessing code.

| Numeric input | Meaning |
| --- | --- |
| `amount_inr` | Payment amount in rupees |
| `hour` | Generated event hour; dashboard source timestamps use UTC |
| `receiver_age_days` | Synthetic receiver-history age |
| `device_trust_score` | Synthetic or manually supplied device-trust value |
| `velocity_1h` | Number of payments in the preceding hour |
| `account_age_days` | Synthetic account age |
| `prior_txn_count` | Synthetic prior-transaction count |
| `urgency_score` | Synthetic or derived urgency indicator |

| Boolean input | Meaning |
| --- | --- |
| `receiver_known` | Whether receiver history is known |
| `merchant_match` | Synthetic/manual expected-to-displayed merchant match |
| `new_device` | Device-change flag |
| `location_changed` | Location-change flag |
| `asks_for_otp` | Message credential-request flag |
| `asks_for_upi_pin` | Message UPI-PIN-request flag |
| `asks_to_scan_qr` | Message mentions QR/scanning |
| `asks_to_install_app` | Message mentions app installation or remote access |
| `qr_scanned` | Whether the example uses a QR |
| `qr_merchant_match` | QR payee-name comparison, when supplied |
| `amount_match` | Encoded-to-intended amount comparison, when supplied |

| Categorical input | Meaning |
| --- | --- |
| `city`, `state` | Synthetic location context |
| `upi_app` | App name assigned to the example |
| `device_type` | Synthetic device description |
| `payment_type` | pay, collect_request, qr, bill_pay, or transfer |
| `channel` | Example communication/payment channel |
| `day_of_week` | Generated weekday |
| `message_present` | Message-availability indicator, converted to a category string |

### Other transaction columns

| Column | Role |
| --- | --- |
| `transaction_id` | Join key; not a model input |
| `user_id` | Split/audit group; not a model input |
| `event_timestamp` | Generated UTC timestamp for inspection and charts |
| `split` | Supplied train/validation/test assignment |
| `receiver_upi_id` | Synthetic identifier; not looked up or sent a payment |
| `merchant_name` | Example merchant label |
| `message_category` | Label-derived annotation; excluded from model inputs |
| `label_fraud` | Binary target |
| `fraud_category` | Ground-truth category annotation; excluded from inputs |
| `source_type` | Generation/demo metadata; excluded from inputs |

### Message table

| Columns | Meaning |
| --- | --- |
| `message_id`, `transaction_id` | Message ID and transaction join key |
| `split` | Supplied transaction-linked split; not the final NLP evaluation split |
| `source_channel` | Synthetic message channel |
| `message_text` | The only input used by the TF-IDF message classifier |
| `category` | Synthetic scenario category; not a text-model input |
| `urgency_score` | Synthetic annotation |
| `asks_for_otp`, `asks_for_upi_pin` | Credential-request annotations |
| `asks_to_scan_qr`, `asks_to_install_app` | QR/app-request annotations |
| `contains_payment_request` | Synthetic request annotation |
| `label_fraud` | Binary target |
| `source_type` | Source/demo metadata |

### QR event table

| Columns | Meaning |
| --- | --- |
| `qr_id`, `transaction_id`, `split` | ID, join key, and supplied split |
| `expected_merchant`, `decoded_merchant` | Expected and synthetic decoded names |
| `expected_upi_id`, `decoded_upi_id` | Synthetic payee identifiers |
| `merchant_match` | Synthetic name-match annotation |
| `amount_match` | Synthetic amount-match annotation |
| `sticker_tampered` | Synthetic tampering flag, not inferred from pixels |
| `qr_source` | Synthetic display/source context |
| `label_fraud`, `fraud_category` | Linked synthetic scenario labels |
| `source_type` | Source metadata |

The QR table contains **records, not a labeled image collection**. Its fraud labels describe the linked synthetic scenario; they are not independent proof that a QR image is tampered. The app does not train a QR image classifier from this table.

## Training and evaluation

### Transaction pipeline

The model is a scikit-learn `Pipeline`:

1. Select the allowed feature columns.
2. Convert boolean flags to 0/1; missing/unrecognized boolean values become 0.
3. Convert numeric inputs to numbers; impute missing values with the training median and apply standard scaling.
4. Convert categorical inputs to strings; apply imputation and one-hot encoding with unknown categories ignored.
5. Fit a Random Forest.

Current Random Forest settings are 300 trees, maximum depth 14, minimum leaf size 2, balanced-subsample class weights, and `random_state=42`.

Preprocessing is fitted only on training data. The pipeline excludes IDs, targets, category answers, and source metadata. In particular, `message_category` is excluded because it exposes the answer.

### Message pipeline

The message model uses:

- Lowercased TF-IDF word unigrams and bigrams.
- `min_df=2`, a maximum of 12,000 features, and sublinear term frequency.
- Logistic regression with balanced class weights, a 1,200-iteration limit, and `random_state=42`.

Only `message_text` enters this classifier. This is not a transformer or an LLM, and the current model predicts binary fraud labels rather than a scam-category probability distribution.

### Evaluation splits

The four `synthetic_demo` transactions/messages are excluded before fitting and evaluation.

| Model | Training rows | Validation rows | Test rows | Separation method |
| --- | ---: | ---: | ---: | --- |
| Transaction | 4,124 | 936 | 940 | User IDs do not cross supplied splits |
| Message | 1,672 | 399 | 459 | Normalized message templates do not cross splits |

For message evaluation, numbers are replaced by a placeholder and whitespace/case are normalized. For example, otherwise identical messages mentioning ₹500 and ₹5,000 belong to the same template group.

There are **51 normalized template groups**: 35 training, eight validation, and eight test. The groups are split approximately 70/15/15 with binary-label stratification. Row shares differ because some templates occur more often.

The NLP split deliberately does not reuse the message CSV's supplied split. Each model has its own evaluation design. The combined risk engine does **not** currently have a separately evaluated end-to-end held-out benchmark.

### Metrics

The classifier decision threshold used for the reported precision, recall, F1, and confusion matrix is **0.50**.

- **Precision:** among predicted fraud examples, how many have a fraud label?
- **Recall:** among fraud-labeled examples, how many were detected?
- **F1:** a combined precision/recall measure.
- **Accuracy:** the fraction of all examples classified correctly; insufficient on its own for imbalanced fraud data.
- **`pr_auc`:** the repository stores scikit-learn's **average precision** under this key. It is a ranking summary, not a calibrated probability or proof of operational accuracy.
- **Confusion matrix:** rows are actual legitimate/fraud, columns are predicted legitimate/fraud.

Validation results are stored separately in `metrics.json`. The current classifiers use the fixed threshold above; validation-based threshold optimization is future work.

Dependency versions are bounded in `requirements.txt` rather than fully locked. Results may change with a different compatible library version.

## How the risk index works

The three possible components are:

| Component | Weight when available | Interpretation |
| --- | ---: | --- |
| Transaction model | 0.55 | Class-1 model output multiplied by 100 |
| Message model | 0.25 | Class-1 model output multiplied by 100; omitted when text is blank |
| Explicit rules | 0.20 | Warning-rule index described below |

For the available set of components:

~~~text
blended_index = sum(component_score * weight) / sum(available_weights)
final_index = round(min(100, max(blended_index, strongest_warning_floor)), 1)
~~~

Unavailable models are excluded from both numerator and denominator. If no models are available, the rule score is the result. QR inspector uses this rules-only route.

The rule score is the larger of the capped sum of rule points and the strongest matching rule's minimum index.

| Observed rule | Points | Minimum index |
| --- | ---: | ---: |
| Collect request with refund/cashback/receive/reward wording | 45 | 85 |
| UPI PIN request flag | 35 | 80 |
| OTP request flag | 35 | 80 |
| App-installation/remote-access wording or flag | 25 | 65 |
| QR merchant mismatch | 40 | 70 |
| QR amount mismatch | 30 | 70 |
| User marks a possible replaced sticker | 25 | 60 |
| Unknown receiver and amount at least ₹10,000 | 15 | None |
| New device and changed location | 12 | None |
| Urgent/account-threat wording | 10 | None |

For example, even if both models output very low scores, a refund/collect contradiction still produces an index of at least 85. An explicit warning is not averaged away.

| Final index | Band | App guidance |
| --- | --- | --- |
| Below 40 | Low | No strong warning detected; still check receiver and amount |
| 40 to below 70 | Medium | Check the context before approving |
| 70 to 100 | High | Pause and verify independently |

These weights, points, thresholds, and floors are **hand-set prototype choices**. They have not been calibrated against real financial outcomes.

Text flags use regular expressions with word boundaries and a limited “never/do not/don't share” exclusion. They can still produce false positives or miss unusual wording, negation, obfuscation, and multilingual scams.

### What the explanations mean

The result's rule descriptions explain directly observed conditions. Extra messages are added when either model component is at least 60/100.

The feature-importance chart describes the Random Forest globally. It is **not** a per-payment attribution, and the app does not implement SHAP, causal explanations, or LLM-generated reasoning.

Programmatic rules-only use from the repository root:

~~~python
from src.fraudshield.risk_engine import analyze_payment
from src.fraudshield.scenarios import SCENARIOS, scenario_features

example = SCENARIOS["Refund trap"]
features, qr_info = scenario_features(example)
result = analyze_payment(features, example["message"], qr_info)

print(result["risk_score"])       # 85.0 for this explicit rule
print(result["risk_band"])        # high
print(result["reasons"])
~~~

The JSON report includes the UTC check timestamp, amount, payment type, risk index/band, recommendation, explanations, rule signals, component outputs, operating mode, and score note. It does not include the original pasted message.

## How QR inspection works

1. Accept PNG/JPEG/WebP bytes or a pasted URI.
2. For images, enforce an **8 MiB** byte limit and a **16-megapixel** size limit.
3. Convert image pixels and decode with OpenCV's QR detector.
4. Require the `upi://pay` scheme/authority combination.
5. Parse `pa` (payee ID), `pn` (payee name), `am` (amount), and `cu` (currency).
6. Reject duplicate values for those fields, an ill-formed payee ID, invalid/non-positive amounts, and non-INR currency.
7. Compare the decoded name and amount with the expected values.

An absent currency defaults to INR. A QR without an encoded amount is treated as a static QR with **unknown amount match**, not automatically fraudulent. Missing expected/decoded names produce an unavailable comparison.

The current name comparison lowercases and removes characters outside ASCII letters/digits. It is a simple comparison heuristic, not legal-name matching, transliteration, fuzzy matching, or a bank registry lookup. Regional-language names and aliases need better handling.

If both an image and URI are supplied, the image takes precedence. No QR link is opened and no payment is initiated. On a hosted deployment, “local decoding” means decoding inside the app's server process, not entirely inside the visitor's browser.

## Graphs and how to read them

| Graph | What it shows | Interpretation limit |
| --- | --- | --- |
| Overview activity | Daily generated transaction totals and fraud-label counts | Fraud labels are a subset of totals; do not add them as independent categories |
| Scam-category bars | Counts of positive scenario labels | Describes this synthetic dataset only |
| Payment-type chart | Fraction of selected rows with fraud labels | Not the real-world risk of that payment type |
| Amount box plots | Amount distributions by synthetic label on a log scale | Large amounts alone do not prove fraud |
| Hourly activity | Counts by generated UTC hour and label | Not the current user's local-time activity |
| Correlation heatmap | Co-occurrence/correlation of selected synthetic flags and labels | Correlation is not causation |
| Risk gauge/components | Current heuristic index and contributing component outputs | Not a calibrated probability |
| Confusion matrix | Correct/incorrect classifier decisions at threshold 0.50 | Applies to that model and holdout design |
| Precision–recall curve | Tradeoff across classifier thresholds | Ranking quality does not choose a deployment threshold |
| Feature importance | Global fitted Random Forest feature importance | Does not explain one payment causally |

## Results and verified checks

The application baseline at commit [b0be162](https://github.com/Athar-cell/PaymentShield/commit/b0be162af13151285522808a5a3bc2f79e88cde3) passed [this GitHub Actions run](https://github.com/Athar-cell/PaymentShield/actions/runs/38695668818).

The recorded synthetic test results are:

| Model | Test examples | Accuracy | Precision | Recall | F1 | Average precision (`pr_auc`) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Transaction | 940 | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| Message | 459 | 71.90% | 45.57% | 100.00% | 62.61% | 100.00% |

**Do not present the transaction result as 100% accurate real-world fraud detection.** The generator used strong label-dependent feature ranges, making the task much easier than real fraud detection.

The message result illustrates a separate issue: it ranks the small holdout well, but the default 0.50 threshold also flags many legitimate examples.

| Model | True negatives | False positives | False negatives | True positives |
| --- | ---: | ---: | ---: | ---: |
| Transaction | 803 | 0 | 0 | 137 |
| Message | 222 | 129 | 0 | 108 |

The message model's **129 false positives** are a concrete improvement target. Threshold selection should use validation data, then be assessed on an untouched test set—not optimized repeatedly on these published test results.

The verified baseline includes **23 passing tests**, model training, and real-browser checks. Test coverage includes dataset completeness, linked transaction IDs, split separation, label-column exclusion, critical warning behavior, QR parsing/image decoding, page rendering, scenario switching, and empty chart selections.

The browser script checks rendered charts and captures overview, payment analysis, risk intelligence, and a mobile viewport. CI retains:

- `paymentshield-evaluation`: `metrics.json`.
- `paymentshield-previews`: website screenshots.

Open a successful run in [GitHub Actions](https://github.com/Athar-cell/PaymentShield/actions) to download them. The CI badge reports the current workflow state; the recorded numbers above refer to the stated baseline.

The tests demonstrate that the implemented scenarios and interfaces work. They are not an independent field evaluation, a penetration test, or validation of production fraud recall.

## Deploy to Streamlit Community Cloud

1. Open [Streamlit Community Cloud](https://share.streamlit.io/) and connect the GitHub account.
2. Choose **Create app** and select the existing repository.
3. Set repository to `Athar-cell/PaymentShield`, branch to `main`, and entrypoint to `app.py`.
4. In advanced settings, choose Python 3.11 to match CI.
5. Deploy and watch the build logs.

The root `requirements.txt` supplies dependencies. No prediction-service secret or AI API key is needed. A model-powered page trains models on first use if the runtime needs them.

After pushing updates, check the connected app and its build logs. Reboot it if necessary. Keep its Python version consistent with the tested environment.

Publishing code to GitHub is not the same as deploying a live app. This README does not claim a public deployment URL has been verified.

See the official [deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy) and [dependency guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).

## Privacy and runtime behavior

- The repository contains synthetic benchmark records, not real customer data.
- The app does not request a bank login, actual OTP, or actual UPI PIN.
- Hosted analysis runs on the Streamlit server; inputs travel to that server.
- Widget values and the latest analysis—including its message text—can remain in session memory. The compact history table omits message text.
- The repository does not implement a database or intentional persistent logging of submitted payment messages.
- Uploaded QR bytes are decoded in memory; this code does not save them as image files.
- Models and aggregate metrics are written to the server's `artifacts/` directory.
- The exported explanation report omits the original message.
- There are no user accounts, access controls, or production retention controls implemented here.
- Host/platform logs and telemetry are separate from this application's session-state behavior.

Use synthetic inputs for public demos. Do not paste real credentials into the message box. Load only trusted pickle model artifacts generated from this repository.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Python or pip command not found | Install a supported Python version and use the virtual environment's full interpreter path |
| `ModuleNotFoundError` for sklearn, Streamlit, cv2, or another dependency | Install requirements with the same interpreter used to run the app |
| Virtual-environment activation blocked in PowerShell | Use `.\.venv\Scripts\python.exe` directly; activation is not required |
| Data file not found | Run from the repository root and keep all eleven transaction parts plus both linked CSVs |
| Invalid labels, missing linked IDs, or count-test failure | Check for an incomplete/truncated CSV download or a missing part |
| “Model training is unavailable” | Expand the setup details; check dependencies, complete data, and write access to `artifacts/` |
| Old or incompatible generated models | Remove only the three generated model/metrics files listed above, restart the app, and retrain |
| A QR cannot be read | Use a clear, front-facing image with its white border, below the size limits |
| Missing QR amount | This can be a static QR; confirm the amount on the payment screen |
| Merchant mismatch | Inspect spelling/aliases and verify independently; a name comparison is not bank identity validation |
| No chart rows | Select a payment type and split containing data |
| No session-history table | It appears after more than one payment analysis |
| A normal message is flagged | Inspect the rule/model components; the current text model and rules have known false positives |
| Streamlit Cloud build fails | Check its logs, entrypoint, Python version, and the root requirements file |

For test failures, inspect [the Actions workflow](.github/workflows/ci.yml) and the failing run's logs. A syntax check alone is not enough to establish working model inference or UI behavior.

## Hackathon presentation guide

### A three-minute walkthrough

| Time | Show | Explain |
| --- | --- | --- |
| 0:00–0:25 | Overview | “We help a user inspect a payment before approving it.” |
| 0:25–1:05 | Refund trap | Explain the collect-request/refund contradiction, the warning floor, and the evidence |
| 1:05–1:30 | Lunch with a friend | Show that ordinary examples can receive a low-risk result |
| 1:30–2:00 | QR inspector | Upload the sample, compare name/amount, and demonstrate a mismatch |
| 2:00–2:25 | Risk intelligence | Filter the benchmark and explain one useful chart |
| 2:25–3:00 | Model lab | Explain the held-out evaluation, false positives, and the next validation step |

Use Demo studio as a reliable shortcut. Rehearse the whole flow on the same device/network used for the presentation and keep a local run available if hosting is unavailable.

### What to say about the contribution

- Multiple evidence channels meet in one understandable payment-screening workflow.
- Explicit warnings remain visible even when a model disagrees.
- A normal-payment example accompanies the scam examples.
- Model evaluation distinguishes disjoint users from disjoint message templates.
- QR inspection reads actual image payloads without initiating a payment.
- The project shows its measured shortcomings instead of hiding them.

### Questions judges may ask

**“Is this real bank data?”**  
No. The included records are synthetic examples informed by public awareness patterns.

**“Why use rules as well as ML?”**  
Rules make explicit contradictions explainable. Models add pattern-based signals. The prototype combines them with visible weights and warning floors.

**“Is the risk index a probability?”**  
No. It combines uncalibrated model outputs and hand-set rules.

**“Why is transaction accuracy perfect?”**  
The generated classes have strongly separated feature ranges. That result does not establish performance on actual UPI payments.

**“What is the biggest measurable weakness?”**  
The message model produces 129 false positives among 351 legitimate held-out examples at threshold 0.50.

**“Does a mismatch prove that a merchant is fraudulent?”**  
No. It tells the user to verify. Names can have aliases and the encoded QR name is self-declared.

**“Does the app monitor my phone?”**  
No. Device/history fields are manual or fixed demo context.

**“Does it block a payment?”**  
No. It displays a recommendation; there is no payment-network integration.

### Suggested team responsibilities

| Workstream | Practical ownership |
| --- | --- |
| Data and ML | Dataset audit, better examples, validation-based thresholds, and evaluation |
| Rules and QR | Message heuristics, URI handling, merchant comparisons, and boundary cases |
| Frontend and UX | Streamlit navigation, explanations, accessibility, and chart clarity |
| Integration and presentation | CI, deployment, demo rehearsal, documentation, and pitch |

These are suggested workstreams, not assignments to named teammates.

## Next development steps

Prioritize evidence quality and measured errors before adding more decorative features.

1. **Improve the benchmark:** obtain appropriately usable real/anonymized evidence, diversify synthetic examples, add difficult legitimate cases, and check event consistency.
2. **Check in the dataset generator:** record its seed, assumptions, version, and validation tests.
3. **Reduce message false positives:** use validation data to select a threshold and evaluate changes on a fresh holdout.
4. **Evaluate the complete engine:** test the final index, weights, and warning floors as one system rather than reporting only component metrics.
5. **Improve text understanding:** handle negation, benign installation/PIN references, spelling variants, multilingual messages, and adversarial wording.
6. **Improve payee verification:** handle aliases/regional names and investigate an authorized merchant identity source.
7. **Improve feature availability:** replace fixed demo context with explicit unknowns or authenticated integration signals.
8. **Improve reproducibility:** lock tested dependency versions and version training artifacts with their data/code provenance.
9. **Prepare any real integration separately:** define authentication, consent, retention, operational review, and bank/API interfaces before accepting real payment data.

FastAPI, a separate JavaScript frontend, or a conversational explanation layer are possible future additions; they are not required to run the current app and are not implemented now.

## Frequently asked questions

**Do I need to train every time I click Analyze?**  
No. Models are cached and saved in the runtime. A new environment, version change, or changed file fingerprint can trigger training again.

**Can I run only the deterministic rules?**  
Yes. Call `analyze_payment()` without models. QR inspector already uses this mode. The app still needs its installed Python dependencies to start.

**Where is the full transaction CSV?**  
The committed source is the eleven parts in [data/transactions](data/transactions). The app combines them; Risk intelligence can export the full selection.

**Are the QR-event records the data behind an image classifier?**  
No. They are synthetic comparison records. OpenCV decodes image payloads; rules compare the decoded fields.

**Why can average precision be 100% while message precision is 45.57%?**  
Average precision summarizes ranking across thresholds. Precision in the results table uses the fixed 0.50 threshold. Good ranking can coexist with a poorly chosen operating threshold.

**Where are the screenshots and training report?**  
In the artifact downloads attached to successful [GitHub Actions runs](https://github.com/Athar-cell/PaymentShield/actions).

**Is there a public live URL?**  
A URL must be obtained and verified through the Streamlit deployment. The GitHub repository alone does not establish a live deployment.

**What license does the project use?**  
The repository does not currently include a LICENSE file. A project/data license has not been specified here.

## References

- [NPCI Fraud Awareness](https://www.npci.org.in/fraud-awareness): awareness patterns informing the synthetic scenarios.
- [NPCI UPI FAQs](https://www.npci.org.in/what-we-do/upi/faqs): payment-flow background.
- [RBI BE(A)WARE](https://cms.rbi.org.in/cms/assets/Documents/BEAWARE07032022.pdf): public fraud-awareness material.
- [Streamlit deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).
- [Streamlit dependency guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).
- [Dataset notes](data/README.md), [model code](src/fraudshield), and [automated workflow](.github/workflows/ci.yml) in this repository.

These sources do not imply endorsement by NPCI, RBI, any bank, or a payment provider.

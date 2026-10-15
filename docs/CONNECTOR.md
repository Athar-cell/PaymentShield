# PaymentShield connector — developer guide

## Product boundary

Our product is the risk-aware checkout and connector. It can gate payments started through
our application. It is not a bank, a personal Google Pay interception plug-in, an issuer
authorization engine, or a production payment gateway. A provider adapter is replaceable;
the current external example is Razorpay **test mode only**. Nothing here moves real money.

## Architecture

```text
Customer / same-origin merchant storefront
              |
         FastAPI service
              |
     Server catalog + session
              |
    Shared feature contract -------- Offline training
              |                         |
      Rules + calibrated model      Validation selection
              |                         |
        Decision policy <---------- Trusted model artifact
        /      |       \
    proceed   review    stop
       |        |        |
       |   Merchant     no provider initiation
       |   clearance
       +--------+
              |
      Payment-provider adapter
       /                   \
 Demo simulator       Optional test provider
                            |
                 Signature + status verification
                            |
 SQLite orders/events <-----+----- Signed webhook
```

The UI is served by the same FastAPI process. No cross-origin browser API is enabled.
SQLite write transactions serialize state changes; this is not a horizontally scaled design.
One process/database is appropriate for a development prototype, not production deployment.

## Start locally

From the repository directory, using Python 3.11:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-connector.txt
.\.venv\Scripts\python.exe scripts/train_checkout.py
$env:PAYMENTSHIELD_PROVIDER = "demo"
$env:PAYMENTSHIELD_MERCHANT_TOKEN = "choose-a-long-random-local-value"
.\.venv\Scripts\python.exe -m uvicorn src.connector.api:app --host 127.0.0.1 --port 8000
```

Linux/macOS equivalents use `python3 -m venv .venv`, `.venv/bin/python`, and
`export PAYMENTSHIELD_MERCHANT_TOKEN=...`. Open http://127.0.0.1:8000 and `/docs` for the API.
Training is offline and explicit. After retraining, restart the service to load the new model.

`.env.example` documents configuration, but is not auto-loaded. Use process environment.
Do not bind this development service publicly and assume it is production hardened.

## Optional external test connection

Only if you want to exercise the included external adapter, set:

```powershell
$env:PAYMENTSHIELD_PROVIDER = "razorpay_test"
$env:RAZORPAY_KEY_ID = "your-test-key-id"
$env:RAZORPAY_KEY_SECRET = "your-test-key-secret"
$env:RAZORPAY_WEBHOOK_SECRET = "your-separate-webhook-secret"
```

Use actual test values locally; never paste them into chat, source, screenshots, or GitHub.
The key ID must begin `rzp_test_`; live keys fail startup. The browser receives only the
publishable key ID, provider order ID, amount and currency. Credentials are used server-side.

In provider-test mode, browser-selected synthetic profiles are ignored. The customer is
anonymous, so its order goes to merchant review. The merchant supplies the configured token
in the review screen, independently checks the order, and explicitly clears it. Customer then
refreshes status and continues to provider checkout. This is a safe test workflow, not a real
customer-verification integration. The merchant note records a human claim, not verified identity.

For webhooks, deploy this service to a suitable public HTTPS staging host with durable storage.
Set `/api/v1/webhooks/razorpay` as the provider webhook endpoint and subscribe to:

- `payment.authorized`
- `payment.captured`
- `payment.failed`

Configure the same separate webhook secret on both sides. Localhost itself cannot receive
external webhooks. Do not open a tunnel or enable live payments without deliberate setup.

Razorpay's test flow is simulated; it does not prove real app handoff to Google Pay. Actual
UPI intent/QR availability depends on provider setup and the supported device/flow. Do not
build a deprecated VPA-collect flow based on old examples.

Official references: [Razorpay Standard Checkout](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/),
[Google Pay support](https://razorpay.com/docs/payments/payment-methods/upi/google-pay/),
[webhook validation](https://razorpay.com/docs/webhooks/validate-test/).

## State model

| State | Meaning | Permitted next action |
| --- | --- | --- |
| ready | Risk-cleared or merchant-cleared | Start checkout before expiry |
| review | More verification is required | Authenticated merchant approve/decline |
| blocked | Policy stopped this order | No checkout or override |
| provider_pending | A provider order exists | Finish provider checkout / await confirmation |
| authorized | Provider authorization, not capture | Await capture / status confirmation |
| paid | Capture confirmed | Terminal; late failures cannot undo paid |
| failed | Simulated or verified provider failure | New order for a new attempt |
| provider_unknown | Order-create request had an uncertain result | Manual provider reconciliation; no blind retry |

Risk approval is not bank authorization. Authorized is not captured. Client-side success and
failure events alone are not trusted as payment outcomes. Browser reload resumes an order
only within the same cookie-bound session. A cookie is not a verified customer identity.

## API contract

- `GET /api/v1/config`: initialize session and read public configuration/catalog.
- `POST /api/v1/orders`: `{item, quantity, scenario, idempotency_key}`. Price comes from server.
  Scenario is demo-only; unknown fields such as `amount` are rejected. The same idempotency
  key/body/session returns the same order; a changed body conflicts.
- `GET /api/v1/orders/{id}`: current session's order and event trail.
- `POST /api/v1/orders/{id}/checkout`: allowed states only; reuse existing provider order.
- `POST /api/v1/orders/{id}/demo-complete?outcome=success|failure`: demo mode only.
- `POST /api/v1/orders/{id}/verify`: signed provider callback fields; server fetches actual
  provider payment and checks order/amount/currency/status.
- `POST /api/v1/webhooks/razorpay`: raw-body HMAC, validated fields, duplicate-body suppression.
- `GET /api/v1/merchant/orders`: requires `Authorization: Bearer <merchant token>`.
- `POST /api/v1/merchant/orders/{id}/review`: `{approve, note}` plus merchant authorization.
- `GET /api/v1/evaluation`: public aggregate synthetic benchmark report, no customer records.

`web/connector-sdk.js` wraps the customer calls. It currently works with the same-origin
catalog/store. It is not a generic cross-site SaaS SDK: no merchant onboarding, arbitrary
amount ingestion, identity propagation, or multi-merchant authorization exists yet.

## Feature provenance

The shared 11-feature contract includes order amount, amount relative to typical purchase,
account age, previous payments, hourly attempts/failures, new device context, UTC hour,
session age, item count, and whether customer history is present. Demo profiles seed the
history. Additional order/failure counts come from the session's actual local records.

In a future authenticated merchant integration, historical features must be derived by a
trusted backend from events available **before** payment time. Do not accept a browser's
claim that it is a known device/customer. Unknown history stays unknown and triggers review.

## Model protocol

1. Reproducibly generate a synthetic stochastic checkout dataset with overlapping legitimate
   and suspicious distributions, unobserved noise, and probabilistic labels.
2. Fit on 7,200 rows / 720 user groups, validate on 2,400 rows / 240 distinct groups.
3. Compare raw/engineered logistic regression, Random Forest and gradient boosting.
4. Calibrate each candidate with sigmoid calibration inside three train-only user-disjoint folds.
5. Select maximum validation average precision, not the best test result.
6. Choose review threshold to maximize validation recall under 5% false-positive rate.
7. Enable model-only blocking only if >=90% validation precision with >=20 flagged examples;
   otherwise use 1.01 to disable it. This condition is not a confidence bound or real-world proof.
8. Evaluate the locked model/policy on a distinct-seed, later-window 2,400-row lockbox with
   240 disjoint users. The development holdout used while building the benchmark is replaced.

Log transforms and interactions in `engineered_features` use pre-payment fields only. Global
thresholds can perform differently across groups; slice reports cover device and amount groups.
Those slices are descriptive diagnostics, not fairness certification.

Confusion matrices use `[[true negatives, false positives], [false negatives, true positives]]`.
The primary matrix is model-only at the review threshold. A separate combined-policy matrix
evaluates whether model OR rules flags an example; it does not measure final fraud prevention.
ROC-AUC, average precision, precision, recall, F1, false-positive rate, Brier score, calibration,
and raw false-alarm/missed-case counts are generated. Thresholds are not tuned on the lockbox.

This benchmark is not evidence that the model is good enough for deployment. Its positive
labels are invented outcomes. The model can miss most suspicious examples at a restrictive
false-positive budget. Do not hide those misses behind overall accuracy. Obtain appropriate
real merchant outcome labels and audit temporal leakage before a production claim.

## Verification

```text
python -m pytest -q
python scripts/train_checkout.py
python -m pip install playwright
python -m playwright install chromium
python scripts/capture_connector.py
```

The browser check covers the paid demo path, merchant approval, direct API attempts to bypass
a block, confusion-matrix rendering, and mobile overflow. API tests additionally cover session
isolation, signatures, amount mismatches, duplicate notifications, live-key rejection, model
failure, expiry, idempotency, provider uncertainty, and data split contracts.

## Remaining production work

Real labelled data; merchant/customer authentication and tenancy; verified historical-feature
ingestion; provider reconciliation and retry policy; challenge/verification integration; user
appeals; secret rotation; distributed anti-abuse controls; load tests; PCI/compliance review by
qualified parties; data retention policy; durable hosting/backup; accessibility/security review.
The service intentionally does not claim these capabilities are complete.

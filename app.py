from __future__ import annotations

from pathlib import Path
import pickle

import streamlit as st

from src.fraudshield.risk_engine import analyze_payment


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
ARTIFACT_DIR = ROOT / "artifacts"


@st.cache_resource
def load_model(path: Path):
    if not path.exists():
        return None
    with path.open("rb") as handle:
        return pickle.load(handle)


st.set_page_config(page_title="FraudShield AI", page_icon="🛡️", layout="wide")
st.title("FraudShield AI")
st.caption("Pre-transaction UPI fraud screening with explainable signals")

tx_model = load_model(ARTIFACT_DIR / "transaction_model.pkl")
msg_model = load_model(ARTIFACT_DIR / "message_model.pkl")
if tx_model is None or msg_model is None:
    st.info("Models are not trained yet. Run: python scripts/train_models.py")

with st.form("payment_form"):
    left, right = st.columns(2)
    with left:
        amount = st.number_input("Amount (INR)", min_value=1.0, value=5000.0, step=100.0)
        payment_type = st.selectbox("Payment type", ["pay", "collect_request", "qr", "bill_pay", "transfer"])
        receiver_known = st.checkbox("Receiver is already known", value=False)
        new_device = st.checkbox("New device", value=True)
        location_changed = st.checkbox("Location changed", value=False)
        device_trust = st.slider("Device trust score", 0, 100, 40)
    with right:
        expected_merchant = st.text_input("Expected merchant", value="Amazon")
        decoded_merchant = st.text_input("Decoded QR merchant", value="Amazon")
        qr_scanned = st.checkbox("QR scanned", value=payment_type == "qr")
        sticker_tampered = st.checkbox("QR sticker may be tampered", value=False)
        message = st.text_area("Paste SMS, WhatsApp, or email text", value="Your refund is ready. Approve the collect request to receive it.")
    submitted = st.form_submit_button("Analyze payment", type="primary")

if submitted:
    merchant_match = expected_merchant.strip().lower() == decoded_merchant.strip().lower()
    features = {
        "amount_inr": amount,
        "hour": 22,
        "receiver_known": receiver_known,
        "receiver_age_days": 10 if not receiver_known else 400,
        "merchant_match": merchant_match,
        "new_device": new_device,
        "location_changed": location_changed,
        "device_trust_score": device_trust,
        "velocity_1h": 3,
        "account_age_days": 500,
        "prior_txn_count": 30,
        "city": "Bengaluru",
        "state": "Karnataka",
        "upi_app": "Google Pay",
        "device_type": "android_personal",
        "payment_type": payment_type,
        "channel": "in_app",
        "day_of_week": "Mon",
        "message_present": bool(message.strip()),
        "message_category": "unknown",
        "urgency_score": 90 if any(word in message.lower() for word in ["urgent", "immediately", "blocked", "today"]) else 10,
        "asks_for_otp": "otp" in message.lower(),
        "asks_for_upi_pin": "upi pin" in message.lower() or "pin" in message.lower(),
        "asks_to_scan_qr": "qr" in message.lower() or "scan" in message.lower(),
        "asks_to_install_app": any(word in message.lower() for word in ["install", "anydesk", "remote", "screen sharing"]),
        "qr_scanned": qr_scanned,
        "qr_merchant_match": merchant_match,
        "amount_match": True,
    }
    result = analyze_payment(
        features,
        message,
        {"sticker_tampered": sticker_tampered},
        transaction_model=tx_model,
        message_model=msg_model,
    )
    color = {"high": "#DC2626", "medium": "#D97706", "low": "#15803D"}[result["risk_band"]]
    st.markdown(f"## Risk: <span style='color:{color}'>{result['risk_band'].upper()}</span>", unsafe_allow_html=True)
    st.metric("Risk score", f"{result['risk_score']}/100")
    if result["risk_band"] == "low":
        st.success(result["recommendation"])
    else:
        st.warning(result["recommendation"])
    st.subheader("Why")
    for reason in result["reasons"]:
        st.write(f"• {reason}")
    st.subheader("Score components")
    st.json(result["components"])


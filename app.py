from __future__ import annotations

import io
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.fraudshield.qr import decode_qr_image, merchant_names_match, parse_upi_payload
from src.fraudshield.data import read_dataset
from src.fraudshield.risk_engine import analyze_payment
from src.fraudshield.scenarios import SCENARIOS, scenario_features
from src.fraudshield.training import MODEL_VERSION, train_models
from src.fraudshield.ui import COLORS, STYLE, TEAL, chart_style, esc, gauge

ROOT = Path(__file__).parent
DATA_DIR, ARTIFACT_DIR = ROOT / "data", ROOT / "artifacts"
PAGES = ["Overview", "Scan & explain", "QR inspector", "Risk intelligence", "Model lab", "Demo studio"]
st.set_page_config(page_title="PaymentShield | Think before you pay", page_icon="🛡", layout="wide")
st.markdown(STYLE, unsafe_allow_html=True)


@st.cache_data
def load_tables():
    return read_dataset(DATA_DIR)


@st.cache_resource(show_spinner="Preparing the demo models. First launch takes a little longer…")
def get_models(version, fingerprint):
    try:
        metrics_path = ARTIFACT_DIR / "metrics.json"
        metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
        if metrics.get("model_version") != version or metrics.get("fingerprint") != fingerprint:
            metrics = train_models(DATA_DIR, ARTIFACT_DIR)
            metrics["fingerprint"] = fingerprint
            metrics_path.write_text(json.dumps(metrics, indent=2))
        with (ARTIFACT_DIR / "transaction_model.pkl").open("rb") as handle:
            tx_model = pickle.load(handle)
        with (ARTIFACT_DIR / "message_model.pkl").open("rb") as handle:
            msg_model = pickle.load(handle)
        return tx_model, msg_model, metrics, None
    except Exception as exc:
        return None, None, None, str(exc)


def nav(page, scenario=None):
    st.session_state["page"] = page
    if scenario:
        st.session_state["scenario"] = scenario
        st.session_state.pop("last_result", None)


def heading(kicker, title, subtitle):
    st.markdown(f'<div class="eyebrow">{esc(kicker)}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<p class="muted">{esc(subtitle)}</p>', unsafe_allow_html=True)


def feature(index, title, body):
    st.markdown(f'<div class="feature"><div class="index">{esc(index)}</div><h4>{esc(title)}</h4><p>{esc(body)}</p></div>', unsafe_allow_html=True)


def plot(fig, key=None):
    st.plotly_chart(fig, width="stretch", key=key)


def result_view(result, values):
    band = result["risk_band"]
    labels = {"high": "Pause and verify", "medium": "Check before approving", "low": "No strong warning detected"}
    st.markdown(f'<div class="result" style="--band:{COLORS[band]}"><div class="eyebrow">{esc(band)} risk · {esc(result["mode"])}</div><h2>{labels[band]}</h2><p>{esc(result["recommendation"])}</p></div>', unsafe_allow_html=True)
    left, right = st.columns([1, 1.8])
    with left:
        plot(gauge(result["risk_score"], band))
        st.caption("Risk index, not a calibrated fraud probability.")
    with right:
        st.subheader("What triggered this result")
        for reason in result["reasons"]:
            st.write("• " + reason)
        if result["rule_floor"]:
            st.caption(f"A direct warning sets a minimum risk index of {result['rule_floor']}/100, even if a model disagrees.")
    with st.expander("See the evidence and model signals", expanded=True):
        signals = result["signals"]
        if signals:
            st.dataframe(pd.DataFrame([{"Signal": s["title"], "Why it matters": s["detail"]} for s in signals]),
                         hide_index=True, width="stretch")
        components = pd.DataFrame([{"Component": k.replace("_", " ").title(), "Score": v}
                                   for k, v in result["components"].items() if v is not None])
        fig = px.bar(components, x="Score", y="Component", orientation="h", range_x=[0, 100],
                     color="Component", text="Score")
        fig.update_layout(showlegend=False)
        plot(chart_style(fig, 245))
        st.caption("The engine blends available model outputs with rules. Explicit warning floors can raise the final index. Missing models are excluded from the blend.")
    cols = st.columns(3)
    for col, (title, body) in zip(cols, [
        ("Verify independently", "Use contact details you already trust, rather than a number supplied in the suspicious message."),
        ("Keep credentials private", "Never share an OTP or UPI PIN with a caller, a message sender, or a support agent."),
        ("Check the payment screen", "Review the receiver and whether the action debits your account before confirming."),
    ]):
        with col:
            feature("NEXT STEP", title, body)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "amount_inr": values["amount"],
              "payment_type": values["payment_type"], **result}
    st.download_button("Download explanation report", json.dumps(report, indent=2),
                       file_name="paymentshield-report.json", mime="application/json")


def overview(transactions, messages, qr_events):
    st.markdown("""
    <div class="hero"><div><div class="eyebrow">PAYMENTSHIELD / PAYMENT INTELLIGENCE</div>
    <h1>A second look.<br>A safer payment.</h1>
    <p>Understand a suspicious request before you approve it. Bring together the payment context, the message, and the QR — then see the reasons behind the warning.</p>
    <span class="pill">Explainable checks · Local QR decoding · Interactive insights</span></div>
    <div class="payment-preview"><div class="eyebrow">EXAMPLE / REFUND REQUEST</div>
    <div class="preview-line"><span>Requested amount</span><span>Collect request</span></div>
    <div class="preview-amount">₹5,000</div>
    <div class="preview-line"><span>“Approve to receive a refund”</span></div>
    <div class="preview-alert"><strong>Pause and verify</strong><br>A refund promise conflicts with a request to debit your account.</div></div></div>
    """, unsafe_allow_html=True)
    a, b, _ = st.columns([1, 1, 2])
    a.button("Check a payment", type="primary", width="stretch", on_click=nav, args=("Scan & explain",))
    b.button("Run the refund demo →", width="stretch", on_click=nav, args=("Scan & explain", "Refund trap"))
    st.write("")
    cards = st.columns(4)
    for col, label, value in zip(cards, ["Transaction examples", "Message examples", "QR examples", "Scam categories"],
                                 [f"{len(transactions):,}", f"{len(messages):,}", f"{len(qr_events):,}",
                                  str(transactions.query("label_fraud == 1")["fraud_category"].nunique())]):
        col.metric(label, value)
    st.caption("All counts describe the synthetic demonstration dataset.")
    st.subheader("Three views of the same payment")
    for col, item in zip(st.columns(3), [
        ("01 / BEHAVIOR", "Does this fit the context?", "Receiver history, amount, device changes, and payment activity."),
        ("02 / LANGUAGE", "What is the message asking?", "Look for a refund trap, credential request, account threat, or remote access."),
        ("03 / PAYEE", "Where does the QR point?", "Decode the payee and amount locally, then compare them with what you expected."),
    ]):
        with col:
            feature(*item)
    l, r = st.columns([1.4, 1])
    with l:
        st.subheader("A day-by-day view of the benchmark")
        daily = transactions.assign(day=pd.to_datetime(transactions["event_timestamp"], utc=True).dt.date)
        daily = daily.groupby("day", as_index=False).agg(Transactions=("label_fraud", "size"), Fraud_labels=("label_fraud", "sum"))
        plot(chart_style(px.area(daily, x="day", y=["Transactions", "Fraud_labels"],
                                labels={"day": "", "value": "Examples", "variable": ""}), 280))
    with r:
        st.subheader("A demo you can explain")
        st.write("1. Open a scam scenario and inspect the payment.")
        st.write("2. Show the exact warning signals and recommendation.")
        st.write("3. Compare it with a normal payment.")
        st.write("4. Open Model lab to discuss measured performance and its limits.")
        st.button("Open demo studio", on_click=nav, args=("Demo studio",))
    st.caption("Research prototype using synthetic examples. The app does not connect to a bank or initiate payments.")


def scan_page(tx_model, msg_model):
    heading("01 / ANALYZE", "Think before you tap.", "Check the payment context and read the evidence behind the result.")
    name = st.selectbox("Showcase scenario", list(SCENARIOS), key="scenario")
    defaults = SCENARIOS[name]
    # A separate form per scenario makes switching examples reliably refresh every field.
    with st.form("payment_" + name):
        l, r = st.columns(2)
        with l:
            st.subheader("Payment context")
            amount = st.number_input("Amount (INR)", min_value=1.0, value=defaults["amount"], max_value=10000000.0)
            payment_type = st.selectbox("Payment type", ["pay", "collect_request", "qr", "bill_pay", "transfer"],
                                       index=["pay", "collect_request", "qr", "bill_pay", "transfer"].index(defaults["payment_type"]))
            known = st.checkbox("Receiver is known", defaults["receiver_known"])
            new_device = st.checkbox("New device", defaults["new_device"])
            location = st.checkbox("Location changed", defaults["location_changed"])
            trust = st.slider("Device trust (demo input)", 0, 100, defaults["device_trust"])
        with r:
            st.subheader("Message and merchant")
            message = st.text_area("Payment message", defaults["message"], height=150)
            expected = st.text_input("Expected merchant", defaults["expected"])
            decoded = st.text_input("Payee name shown by QR or payment app", defaults["decoded"])
            tampered = st.checkbox("Sticker appears replaced", defaults["sticker_tampered"])
            st.caption("Device trust is supplied for this demo; it is not measured from your browser. Use QR inspector to decode an actual image.")
        with st.expander("More transaction context"):
            hour = st.slider("Hour of payment", 0, 23, 13)
            velocity = st.number_input("Payments in the preceding hour", 0, 100, 1)
            receiver_age = st.number_input("Receiver history (days)", 0, 5000, 500 if known else 10)
        submit = st.form_submit_button("Analyze payment →", type="primary", width="stretch")
    if submit:
        values = {"amount": amount, "payment_type": payment_type, "receiver_known": known,
                  "new_device": new_device, "location_changed": location, "device_trust": trust,
                  "expected": expected, "decoded": decoded, "sticker_tampered": tampered, "message": message,
                  "hour": hour, "velocity_1h": velocity, "receiver_age_days": receiver_age}
        features, qr_info = scenario_features(values)
        result = analyze_payment(features, message, qr_info, tx_model, msg_model)
        st.session_state["last_result"] = (name, result, values)
        history = st.session_state.setdefault("history", [])
        history.append({"Check": len(history) + 1, "Scenario": name, "Risk index": result["risk_score"], "Band": result["risk_band"]})
    saved = st.session_state.get("last_result")
    if saved and saved[0] == name:
        result_view(saved[1], saved[2])
    history = st.session_state.get("history", [])
    if len(history) > 1:
        with st.expander("This session's checks"):
            st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch")
            st.caption("Held only in this browser session. Message text is not included in the history.")


@st.cache_data
def sample_qr():
    import qrcode
    buffer = io.BytesIO()
    qrcode.make("upi://pay?pa=demo@example&pn=Demo%20Merchant&am=250&cu=INR").save(buffer, format="PNG")
    return buffer.getvalue()


def qr_page(tx_model, msg_model):
    heading("02 / QR INSPECTOR", "Know where the QR points.", "Read the encoded payee and amount without opening a payment app.")
    l, r = st.columns([1.3, 1])
    with l:
        upload = st.file_uploader("Upload a QR image", type=["png", "jpg", "jpeg", "webp"])
        payload = st.text_input("Or paste a UPI URI", placeholder="upi://pay?pa=...&pn=...")
        expected = st.text_input("Merchant you expect to pay", value="Demo Merchant")
        intended = st.number_input("Intended amount (INR)", min_value=1.0, value=250.0)
        inspect = st.button("Inspect QR", type="primary")
    with r:
        feature("LOCAL INSPECTION", "Read it. Compare it.", "The image is decoded in the app process. The QR is never opened, and no payment is initiated. Its encoded name is self-declared, not verified by a bank.")
        st.download_button("Download a sample QR", sample_qr(), "paymentshield-demo-qr.png", "image/png")
        st.caption("Sample uses the placeholder demo@example. Do not attempt to pay it.")
    if inspect:
        try:
            if upload:
                decoded = decode_qr_image(upload.getvalue())
            elif payload.strip():
                decoded = parse_upi_payload(payload)
            else:
                st.warning("Choose an image or paste a UPI URI first.")
                return
        except Exception as exc:
            st.error(str(exc))
            return
        match = merchant_names_match(expected, decoded["payee_name"])
        amount_match = None if decoded["amount_inr"] is None else abs(decoded["amount_inr"] - intended) < 0.005
        st.subheader("Decoded payment details")
        st.dataframe(pd.DataFrame([{"Payee": decoded["payee_name"] or "Not provided",
                                   "UPI ID": decoded["payee_upi_id"], "Encoded amount": decoded["amount_inr"],
                                   "Currency": decoded["currency"]}]), hide_index=True, width="stretch")
        if match is None:
            st.info("The expected or encoded payee name is missing, so a name comparison is unavailable.")
        if amount_match is None:
            st.info("This is a static QR without an encoded amount. Verify the amount on the payment screen.")
        values = {**SCENARIOS["Lunch with a friend"], "amount": intended, "payment_type": "qr",
                  "expected": expected, "decoded": decoded["payee_name"], "message": "", "amount_match": amount_match}
        features, qr_info = scenario_features(values)
        result = analyze_payment(features, "", qr_info, transaction_model=None, message_model=None)
        result_view(result, values)


def intelligence(transactions, messages, qr_events):
    heading("03 / RISK INTELLIGENCE", "See the patterns behind the labels.", "Explore the synthetic benchmark. Filters update every chart below.")
    c1, c2 = st.columns(2)
    selected = c1.multiselect("Payment types", sorted(transactions["payment_type"].unique()),
                              default=sorted(transactions["payment_type"].unique()))
    split = c2.selectbox("Dataset split", ["All", "train", "validation", "test"])
    view = transactions[transactions["payment_type"].isin(selected)].copy()
    if split != "All":
        view = view[view["split"] == split]
    if view.empty:
        st.info("Select at least one payment type with records.")
        return
    cols = st.columns(4)
    for col, label, value in zip(cols, ["Examples selected", "Fraud labels", "Labeled fraud share", "Median amount"],
                                 [f"{len(view):,}", f"{int(view.label_fraud.sum()):,}",
                                  f"{view.label_fraud.mean():.1%}", f"₹{view.amount_inr.median():,.0f}"]):
        col.metric(label, value)
    l, r = st.columns([1.2, 1])
    with l:
        st.subheader("What kinds of scams?")
        counts = view.query("label_fraud == 1")["fraud_category"].value_counts().rename_axis("Category").reset_index(name="Examples")
        counts["Category"] = counts["Category"].str.replace("_", " ").str.title()
        plot(chart_style(px.bar(counts.sort_values("Examples"), x="Examples", y="Category", orientation="h", color_discrete_sequence=[TEAL]), 365))
    with r:
        st.subheader("Fraud share by payment type")
        rates = view.groupby("payment_type", as_index=False).agg(Share=("label_fraud", "mean"), Examples=("label_fraud", "size"))
        rates["Share"] *= 100
        plot(chart_style(px.bar(rates, x="payment_type", y="Share", hover_data=["Examples"], range_y=[0, 100],
                               labels={"payment_type": "", "Share": "Fraud labels (%)"}), 365))
    l, r = st.columns(2)
    with l:
        st.subheader("Amounts across the two classes")
        labeled = view.assign(Label=view.label_fraud.map({0: "Legitimate label", 1: "Fraud label"}))
        plot(chart_style(px.box(labeled, x="Label", y="amount_inr", color="Label", log_y=True,
                               labels={"amount_inr": "Amount INR (log scale)", "Label": ""}, points=False), 330))
    with r:
        st.subheader("Activity by hour")
        hourly = view.groupby(["hour", "label_fraud"]).size().reset_index(name="Examples")
        hourly["Label"] = hourly.label_fraud.map({0: "Legitimate label", 1: "Fraud label"})
        plot(chart_style(px.line(hourly, x="hour", y="Examples", color="Label", markers=True,
                                labels={"hour": "Hour (UTC in the generated dataset)"}), 330))
    st.subheader("Signals occurring together")
    features = ["new_device", "location_changed", "receiver_known", "merchant_match", "label_fraud"]
    corr = view[features].astype(float).corr().fillna(0)
    plot(chart_style(px.imshow(corr, text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1,
                              labels={"color": "Correlation"}), 380))
    st.caption("Correlation describes this generator's assumptions. It does not establish causality or real UPI fraud prevalence.")
    with st.expander("Inspect the selected records"):
        st.dataframe(view[["transaction_id", "split", "amount_inr", "payment_type", "fraud_category"]].head(200),
                     hide_index=True, width="stretch")
        st.caption("Preview limited to 200 rows. Download includes the complete filtered selection.")
        st.download_button("Download filtered examples", view.to_csv(index=False), "paymentshield-filtered.csv", "text/csv")


def model_lab(metrics):
    heading("04 / MODEL LAB", "Evidence you can stand behind.", "Measured results, evaluation design, and the limits of a synthetic benchmark.")
    if metrics is None:
        st.info("Models are unavailable. The deterministic payment rules and QR inspector still work.")
        return
    st.info("These are synthetic-data benchmark results. They do not measure bank-grade detection accuracy.")
    model = st.selectbox("Evaluation model", ["transaction_model", "message_model"],
                         format_func=lambda x: x.replace("_", " ").title())
    values = metrics[model]
    for col, key in zip(st.columns(4), ["precision", "recall", "f1", "pr_auc"]):
        col.metric(key.replace("_", " ").upper(), f"{values[key]:.1%}")
    st.caption(f"Held-out examples: {values['rows']:,} · Fraud labels: {values['positive_rows']:,} · Classifier decision threshold: 0.50")
    l, r = st.columns(2)
    with l:
        st.subheader("Where did the model get it right?")
        fig = px.imshow(values["confusion_matrix"], text_auto=True,
                        x=["Predicted legitimate", "Predicted fraud"], y=["Actual legitimate", "Actual fraud"],
                        color_continuous_scale="Teal", labels={"color": "Examples"}, aspect="auto")
        plot(chart_style(fig, 340))
    with r:
        st.subheader("Precision and recall tradeoff")
        curve = values["pr_curve"]
        fig = go.Figure(go.Scatter(x=curve["recall"], y=curve["precision"], mode="lines", line={"color": TEAL, "width": 3}))
        fig.update_xaxes(title="Recall", range=[0, 1.02])
        fig.update_yaxes(title="Precision", range=[0, 1.02])
        plot(chart_style(fig, 340))
    st.subheader("What does the transaction model use?")
    importance = pd.DataFrame(list(metrics["feature_importance"].items()), columns=["Feature", "Importance"]).head(10)
    plot(chart_style(px.bar(importance.sort_values("Importance"), x="Importance", y="Feature", orientation="h",
                           color_discrete_sequence=[TEAL]), 340))
    st.caption("Global feature importance describes this fitted model overall. The warning explanations on an individual payment are observable rule signals, not SHAP explanations.")
    l, r = st.columns(2)
    with l:
        feature("EVALUATION DESIGN", "Keep the answers out of the inputs", "Fraud category, message category, labels, IDs, and source metadata are excluded. Transactions are split by user; messages are split by normalized sentence template.")
        st.json(metrics["data"], expanded=False)
    with r:
        for limitation in metrics["limitations"]:
            st.write("• " + limitation)
        st.caption("Validation is reported separately; held-out test results are displayed above. The combined risk index is a heuristic, not an evaluated calibrated model.")


def studio(tx_model, msg_model):
    heading("05 / DEMO STUDIO", "Four stories. One clear explanation.", "Walk through the showcase, then open any example to change its inputs.")
    for name, values in SCENARIOS.items():
        features, qr_info = scenario_features(values)
        result = analyze_payment(features, values["message"], qr_info, tx_model, msg_model)
        with st.container(border=True):
            left, middle, right = st.columns([2, 1, 2])
            left.subheader(name)
            left.write(values["story"])
            middle.metric("Risk index", f"{result['risk_score']:.0f}/100")
            right.write(result["recommendation"])
            with st.expander("Why this result?"):
                for reason in result["reasons"]:
                    st.write("• " + reason)
            st.button("Explore this example →", key="demo_" + name, on_click=nav, args=("Scan & explain", name))
    st.caption("Expected showcase: three high-risk examples and one low-risk example. No actual payment is performed.")


with st.sidebar:
    st.markdown('<div class="brand"><span class="brand-mark">S</span>PaymentShield</div>', unsafe_allow_html=True)
    st.caption("YOUR SECOND LOOK BEFORE PAYING")
    st.radio("Workspace", PAGES, key="page", label_visibility="collapsed")
    st.divider()
    st.caption("Hackathon research prototype")
    st.caption("Synthetic examples. Explainable checks.")
    st.link_button("View project on GitHub", "https://github.com/Athar-cell/PaymentShield")

try:
    transactions, messages, qr_events = load_tables()
except Exception as exc:
    st.error(f"The dataset could not be loaded: {exc}")
    st.stop()

page = st.session_state["page"]
# The overview and dataset charts do not require expensive model initialization.
tx_model = msg_model = metrics = model_error = None
if page in ["Scan & explain", "Model lab", "Demo studio"]:
    fingerprint = [f"{p.relative_to(DATA_DIR)}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in sorted(DATA_DIR.rglob("*.csv"))]
    tx_model, msg_model, metrics, model_error = get_models(MODEL_VERSION, fingerprint)
    if model_error:
        st.warning("Model training is unavailable in this session. Explicit safety rules remain active.")
        with st.expander("Model setup details"):
            st.code(model_error)

if page == "Overview":
    overview(transactions, messages, qr_events)
elif page == "Scan & explain":
    scan_page(tx_model, msg_model)
elif page == "QR inspector":
    qr_page(tx_model, msg_model)
elif page == "Risk intelligence":
    intelligence(transactions, messages, qr_events)
elif page == "Model lab":
    model_lab(metrics)
else:
    studio(tx_model, msg_model)

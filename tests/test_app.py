from streamlit.testing.v1 import AppTest
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def open_page(page):
    app = AppTest.from_file(APP_PATH, default_timeout=120).run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception
    return app


def test_overview_and_navigation():
    app = AppTest.from_file(APP_PATH, default_timeout=120).run()
    assert not app.exception
    assert len(app.metric) == 4


def test_analyzer_switches_scenarios_and_explains():
    app = open_page("Scan & explain")
    app.selectbox(key="scenario").set_value("Lunch with a friend").run()
    amount = next(item for item in app.number_input if item.label == "Amount (INR)")
    assert amount.value == 250
    app.button[0].click().run()
    assert not app.exception
    assert any("No strong warning detected" in item.value for item in app.markdown)
    app.selectbox(key="scenario").set_value("Refund trap").run()
    amount = next(item for item in app.number_input if item.label == "Amount (INR)")
    assert amount.value == 5000
    app.button[0].click().run()
    assert not app.exception
    assert any("Pause and verify" in item.value for item in app.markdown)


def test_analytics_empty_filter_is_handled():
    app = open_page("Risk intelligence")
    app.multiselect[0].set_value([]).run()
    assert not app.exception
    assert app.info


def test_other_sections_render():
    for page in ["QR inspector", "Model lab", "Demo studio"]:
        open_page(page)


def test_qr_uri_is_decoded_in_the_interface():
    app = open_page("QR inspector")
    app.text_input[0].set_value("upi://pay?pa=demo@example&pn=Wrong%20Payee&am=400&cu=INR")
    app.button[0].click().run()
    assert not app.exception
    assert any("QR amount mismatch" in str(item.value) for item in app.dataframe)

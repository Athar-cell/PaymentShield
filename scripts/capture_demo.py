"""Browser smoke check and screenshots in CI; no deployed site is modified."""
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright


def main():
    output = Path("artifacts/previews")
    output.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless=true",
         "--server.address=127.0.0.1", "--server.port=8501", "--browser.gatherUsageStats=false"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(45):
            try:
                urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=2)
                break
            except OSError:
                time.sleep(1)
        with sync_playwright() as browser_api:
            browser = browser_api.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 2400}, device_scale_factor=1)
            page.goto("http://127.0.0.1:8501", wait_until="networkidle")
            page.get_by_text("A second look.", exact=False).wait_for(timeout=120000)
            page.get_by_test_id("stMetricValue").filter(has_text="6,004").wait_for(timeout=120000)
            wait_for_charts(page, 1)
            page.screenshot(path=str(output / "overview.png"), full_page=True)
            page.get_by_text("Scan & explain", exact=True).click()
            page.get_by_role("button", name="Analyze payment →", exact=True).click(timeout=120000)
            page.get_by_role("heading", name="Pause and verify", exact=True).wait_for(timeout=120000)
            wait_for_charts(page, 2)
            page.screenshot(path=str(output / "payment-analysis.png"), full_page=True)
            page.get_by_text("Risk intelligence", exact=True).click()
            page.get_by_text("Signals occurring together", exact=True).wait_for(timeout=120000)
            wait_for_charts(page, 5)
            page.screenshot(path=str(output / "risk-intelligence.png"), full_page=True)
            mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1, is_mobile=True)
            mobile.goto("http://127.0.0.1:8501", wait_until="networkidle")
            mobile.get_by_test_id("stMetricValue").filter(has_text="6,004").wait_for(timeout=120000)
            wait_for_charts(mobile, 1)
            mobile.screenshot(path=str(output / "mobile.png"), full_page=True)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=15)


def wait_for_charts(page, count):
    page.wait_for_function(
        """count => {
            const charts = [...document.querySelectorAll('[data-testid="stPlotlyChart"] .js-plotly-plot')];
            return charts.length >= count && charts.every(chart => chart.querySelector('svg.main-svg'));
        }""", arg=count, timeout=120000)


if __name__ == "__main__":
    main()

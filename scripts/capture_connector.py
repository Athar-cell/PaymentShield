"""Exercise a real browser against the local connector, never a live payment account."""
from pathlib import Path
import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts/checkout/previews"
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PAYMENTSHIELD_PROVIDER": "demo",
           "PAYMENTSHIELD_DB": str(output / f"qa-{time.time_ns()}.sqlite3"),
           "PAYMENTSHIELD_MERCHANT_TOKEN": "browser-qa-fixture-not-a-secret"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.connector.api:app", "--host", "127.0.0.1", "--port", "8002"],
                              cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen("http://127.0.0.1:8002/api/v1/config", timeout=2)
                break
            except OSError:
                time.sleep(.5)
        with sync_playwright() as api:
            browser = api.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://127.0.0.1:8002", wait_until="networkidle")
            page.locator("#mode").filter(has_text="DEMO MODE").wait_for()
            page.screenshot(path=str(output / "checkout.png"), full_page=True)
            page.locator("#assess").click()
            page.locator("#order-state").filter(has_text="ready").wait_for()
            page.locator("#pay").click()
            page.locator("#demo-bank").wait_for(state="visible")
            page.locator("#demo-success").click()
            page.locator("#order-state").filter(has_text="paid").wait_for()
            page.screenshot(path=str(output / "paid.png"), full_page=True)
            page.locator("#scenario").select_option("new_customer")
            page.locator("#assess").click()
            page.locator("#order-state").filter(has_text="review").wait_for()
            review_id = page.locator("#order-id").inner_text()
            assert page.locator("#pay").is_hidden()
            page.get_by_role("button", name="Merchant review").click()
            page.locator("#merchant-token").fill(env["PAYMENTSHIELD_MERCHANT_TOKEN"])
            page.locator("#load-orders").click()
            page.get_by_role("button", name="Clear for checkout").wait_for()
            page.locator(".review-item input").fill("Independently confirmed the synthetic test order.")
            page.get_by_role("button", name="Clear for checkout").click()
            page.wait_for_function("!document.querySelector('.review-item input')")
            page.get_by_role("button", name="Checkout playground").click()
            page.locator("#refresh-order").click()
            page.locator("#order-state").filter(has_text="ready").wait_for()
            page.locator("#scenario").select_option("burst")
            page.locator("#assess").click()
            page.locator("#order-state").filter(has_text="blocked").wait_for()
            blocked_id = page.locator("#order-id").inner_text()
            assert page.request.post(f"http://127.0.0.1:8002/api/v1/orders/{blocked_id}/checkout").status == 409
            page.screenshot(path=str(output / "blocked.png"), full_page=True)
            page.get_by_role("button", name="Model evaluation").click()
            page.get_by_role("heading", name="Confusion matrix · model only").wait_for()
            assert page.locator("#model-content svg").count() == 3
            page.screenshot(path=str(output / "model-evaluation.png"), full_page=True)
            mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            mobile.goto("http://127.0.0.1:8002", wait_until="networkidle")
            mobile.locator("#assess").wait_for()
            assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
            mobile.screenshot(path=str(output / "mobile.png"), full_page=True)
            assert not errors, errors
            print("Browser checks passed: paid flow, merchant review, hard-block bypass, confusion matrix, mobile layout.")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=15)


if __name__ == "__main__":
    main()

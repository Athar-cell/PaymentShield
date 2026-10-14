"""Submit ONE user-specified pending payment to your local PaymentShield service.

This is an API client, not a transaction generator or payment executor.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.live.client import PaymentShieldClient

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", default="http://127.0.0.1:8000")
parser.add_argument("--payee", required=True)
parser.add_argument("--expected-payee")
parser.add_argument("--amount-paise", type=int, required=True)
parser.add_argument("--customer", required=True, help="Pseudonymous authenticated customer reference; not a phone number")
parser.add_argument("--device")
parser.add_argument("--environment", choices=["test", "live"], default="test")
args = parser.parse_args()
client = PaymentShieldClient(args.url, os.getenv("PAYMENTSHIELD_INTEGRATION_ID", "my-app"), os.getenv("PAYMENTSHIELD_INTEGRATION_SECRET", ""))
try:
    result = client.assess({"transaction_id": "intent_" + uuid.uuid4().hex, "customer_ref": args.customer,
                           "device_ref": args.device, "payee": args.payee, "expected_payee": args.expected_payee,
                           "amount_paise": args.amount_paise, "environment": args.environment})
    result.pop("permit", None)
    print(json.dumps(result, indent=2))
finally:
    client.close()

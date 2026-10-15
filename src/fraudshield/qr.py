"""Decode a QR locally without opening links or initiating payments."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


def parse_upi_payload(payload: str) -> dict:
    parsed = urlparse(payload.strip())
    if parsed.scheme.lower() != "upi" or parsed.netloc.lower() != "pay":
        raise ValueError("This QR is not a UPI payment URI. No link has been opened.")
    fields = parse_qs(parsed.query, keep_blank_values=True)
    for key in ("pa", "pn", "am", "cu"):
        if len(fields.get(key, [])) > 1:
            raise ValueError(f"Ambiguous QR: duplicate {key} values.")
    payee = fields.get("pa", [""])[0].strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+", payee):
        raise ValueError("The QR does not contain a well-formed payee UPI ID.")
    amount = fields.get("am", [""])[0]
    if amount and (not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", amount) or float(amount) <= 0):
        raise ValueError("The amount in this QR is invalid.")
    currency = fields.get("cu", ["INR"])[0]
    if currency.upper() != "INR":
        raise ValueError("Only INR payment QR codes are supported in this demo.")
    return {"payee_upi_id": payee, "payee_name": fields.get("pn", [""])[0],
            "amount_inr": float(amount) if amount else None, "currency": "INR", "payload": payload.strip()}


def decode_qr_image(data: bytes) -> dict:
    import cv2
    import numpy as np
    from PIL import Image
    from io import BytesIO

    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Please choose a QR image smaller than 8 MB.")
    with Image.open(BytesIO(data)) as image:
        if image.width * image.height > 16_000_000:
            raise ValueError("Please choose an image below 16 megapixels.")
        pixels = np.array(image.convert("RGB"))
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR))
    if not decoded:
        raise ValueError("No readable QR was found. Try a clear image including its white border.")
    return parse_upi_payload(decoded)


def merchant_names_match(expected: str, decoded: str) -> bool | None:
    def normalize(text):
        return re.sub(r"[^a-z0-9]", "", text.casefold())
    if not expected.strip() or not decoded.strip():
        return None
    return normalize(expected) == normalize(decoded)

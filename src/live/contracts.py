from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

REF = r"^[A-Za-z0-9_-]{4,100}$"
VPA = r"^[A-Za-z0-9._-]{1,120}@[A-Za-z0-9.-]{1,80}$"


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transaction_id: str = Field(pattern=REF)
    customer_ref: str = Field(pattern=REF, description="Pseudonymous ID from your authenticated backend, never a phone number.")
    amount_paise: int = Field(ge=1, le=100_000_000)
    payee: str = Field(pattern=VPA)
    expected_payee: str | None = Field(default=None, pattern=VPA)
    expected_amount_paise: int | None = Field(default=None, ge=1, le=100_000_000)
    device_ref: str | None = Field(default=None, pattern=REF)
    message: str = Field(default="", max_length=3000)
    purpose: str = Field(default="purchase", pattern=r"^(purchase|transfer|receive_refund)$")
    environment: str = Field(default="test", pattern=r"^(test|live)$")


class Authorize(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    intent: Intent
    permit: str = Field(min_length=40, max_length=300)


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transaction_id: str = Field(pattern=REF)
    event_id: str = Field(pattern=REF)
    status: str = Field(pattern=r"^(paid|failed)$")
    provider_reference: str = Field(min_length=4, max_length=120)


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transaction_id: str = Field(pattern=REF)
    label: int = Field(ge=0, le=1)
    evidence: str = Field(min_length=20, max_length=1000)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transaction_id: str = Field(pattern=REF)
    evidence: str = Field(min_length=20, max_length=1000)


class ManualCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    amount_paise: int = Field(ge=1, le=100_000_000)
    payee: str = Field(pattern=VPA)
    expected_payee: str | None = Field(default=None, pattern=VPA)
    expected_amount_paise: int | None = Field(default=None, ge=1, le=100_000_000)
    message: str = Field(default="", max_length=3000)
    purpose: str = Field(default="purchase", pattern=r"^(purchase|transfer|receive_refund)$")


class QRCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    payload: str = Field(min_length=6, max_length=4096)
    amount_paise: int = Field(ge=1, le=100_000_000)
    expected_payee: str | None = Field(default=None, pattern=VPA)
    message: str = Field(default="", max_length=3000)
    purpose: str = Field(default="purchase", pattern=r"^(purchase|transfer|receive_refund)$")


class QRImageCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    image_base64: str = Field(min_length=8, max_length=11_184_812)
    amount_paise: int = Field(ge=1, le=100_000_000)
    expected_payee: str | None = Field(default=None, pattern=VPA)
    message: str = Field(default="", max_length=3000)
    purpose: str = Field(default="purchase", pattern=r"^(purchase|transfer|receive_refund)$")

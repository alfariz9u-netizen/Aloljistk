"""Pydantic request/response schemas, including strict validation for
whatever the AI extraction service returns -- the backend NEVER trusts
raw AI output; it must pass this schema before touching the database."""
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.services.cities import normalize_city


class UpsertUserIn(BaseModel):
    telegram_id: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    # Referrer's telegram_id, captured from a Telegram deep-link start
    # parameter (see bot/handlers/start.py). Only ever applied when this
    # call creates a brand-new user row -- see api/users.py.
    referral_code: str | None = Field(default=None, max_length=32)


class SetRoleIn(BaseModel):
    telegram_id: str
    role: Literal["CARRIER", "SHIPPER"]


class TruckCreateIn(BaseModel):
    telegram_id: str
    truck_type: str | None = Field(default=None, max_length=60)
    current_city: str = Field(min_length=1, max_length=60)
    desired_destination: str | None = Field(default=None, max_length=60)
    available: bool = True
    has_current_trip: bool = False
    trip_origin: str | None = Field(default=None, max_length=60)
    trip_destination: str | None = Field(default=None, max_length=60)
    trip_eta_minutes_from_now: int | None = Field(default=None, ge=0, le=24 * 60)

    @field_validator("current_city", "desired_destination", "trip_origin", "trip_destination")
    @classmethod
    def _norm_city(cls, v):
        return normalize_city(v) if v else v


class LoadCreateIn(BaseModel):
    telegram_id: str
    origin_city: str = Field(min_length=1, max_length=60)
    destination_city: str = Field(min_length=1, max_length=60)
    truck_type: str | None = Field(default=None, max_length=60)
    truck_count: int = Field(default=1, ge=1, le=50)
    loading_time: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    raw_text: str | None = Field(default=None, max_length=1000)

    @field_validator("origin_city", "destination_city")
    @classmethod
    def _norm_city(cls, v):
        return normalize_city(v)


class ExtractIn(BaseModel):
    telegram_id: str
    text: str = Field(min_length=1, max_length=1000)


class ExtractedLoad(BaseModel):
    """Strict schema the AI's JSON output must conform to. Any field the
    AI omits or gets wrong is null/default, never guessed by this layer."""
    type: Literal["load", "truck", "unknown"] = "unknown"
    origin: Optional[str] = None
    destination: Optional[str] = None
    truck_count: Optional[int] = Field(default=None, ge=1, le=50)
    truck_type: Optional[str] = None
    loading_time: Optional[str] = None
    available: Optional[bool] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class InterestIn(BaseModel):
    telegram_id: str
    load_id: uuid.UUID
    truck_id: uuid.UUID


class AdminActionIn(BaseModel):
    telegram_id: str  # must resolve to an ADMIN user server-side
    action: Literal["contact", "connect", "reject", "cancel_load", "resend_notification", "confirm_payment", "grant_subscription"]
    match_id: uuid.UUID | None = None
    load_id: uuid.UUID | None = None
    # For "grant_subscription": the CARRIER's telegram_id to activate a
    # subscription for. Not tied to a specific match, unlike the other
    # actions -- a subscription applies to the carrier account itself.
    target_telegram_id: str | None = None
    # For "grant_subscription": how many days the subscription lasts.
    # Omit (null) for an indefinite subscription with no expiry.
    subscription_days: int | None = Field(default=None, ge=1, le=3650)

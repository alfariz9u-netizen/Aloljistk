"""
Payment provider abstraction -- the extension point for Phase 2
(automated payment collection), not implemented yet.

Phase 1 ships with services/payments.py's manual, admin-confirmed flow
only: nothing here is called or wired up. When ready to automate
collection (Freedom Pay Kyrgyzstan is the leading candidate -- it
already supports in-Telegram card payments, see README.md's payment
section), implement PaymentProvider below for that gateway and:

  1. Register it (e.g. a `get_payment_provider()` factory keyed off a
     new PAYMENT_METHOD setting).
  2. Add a route the gateway calls back to (a webhook endpoint under
     /internal/payments/webhook, verified via verify_webhook() below --
     follow the same pattern as api/telegram_webhook.py: a shared
     secret / signature check before trusting anything in the payload).
  3. On a verified successful-payment webhook, look up the Payment row
     by provider_reference and set it CONFIRMED -- the exact same field
     transition services.payments.confirm_payment_manual() does, just
     triggered by a webhook instead of an admin tap.

Nothing in services/payments.py's gating logic (is_contact_unlocked)
or api/admin.py's "contact" action needs to change -- they already only
care whether a Payment row is CONFIRMED, not how it got that way.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.models import Payment


@dataclass
class ChargeResult:
    """What create_charge() hands back to the caller -- typically a URL
    or deep link the bot sends the payer to complete payment, plus the
    gateway's own reference for this charge (stored on
    Payment.provider_reference so a later webhook can be matched back
    to the right row)."""
    provider_reference: str
    payment_url: str


@dataclass
class WebhookResult:
    """What verify_webhook() hands back after validating an incoming
    gateway callback -- enough for the caller to find the right Payment
    row and update its status."""
    provider_reference: str
    succeeded: bool
    raw_status: str  # the gateway's own status string, kept for logging/audit


class PaymentProvider(ABC):
    """One implementation per gateway. Phase 2's first (and so far only
    planned) implementation would be FreedomPayProvider."""

    @abstractmethod
    async def create_charge(self, payment: Payment) -> ChargeResult:
        """Initiates a charge with the provider for this Payment's
        amount/currency. Must NOT mark the Payment CONFIRMED itself --
        only a verified webhook (via verify_webhook) does that."""
        raise NotImplementedError

    @abstractmethod
    async def verify_webhook(self, raw_body: bytes, headers: dict) -> WebhookResult:
        """Validates the authenticity of an incoming webhook (signature/
        secret check -- never trust an unverified payload) and parses it
        into a WebhookResult. Raise on invalid signatures rather than
        returning succeeded=False, so the caller can reject the request
        outright (mirrors api/telegram_webhook.py's pattern)."""
        raise NotImplementedError

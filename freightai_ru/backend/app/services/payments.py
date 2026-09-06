"""
Payment business logic for the contact-reveal fee (Phase 2 monetization).

Design goal: Phase 1 (manual, admin-confirmed) and Phase 2 (automated
gateway, e.g. Freedom Pay Kyrgyzstan) share the exact same data model,
gating logic, and unlock condition -- see models.Payment. The ONLY thing
that differs between phases is *how* a Payment row gets from PENDING to
CONFIRMED: an admin tap (Phase 1, implemented here) vs. a gateway
webhook (Phase 2, see services/payment_providers.py for the extension
point). Nothing in api/admin.py's gating logic needs to change when
Phase 2 ships.

Dormant unless settings.payments_enabled is True -- every function here
is a no-op-safe check away from Phase 1's free-forever behavior.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import Load, Match, Payment, PaymentStatus, Truck
from app.services.trusted_pairs import is_trusted_pair


def determine_payer_user_id(load: Load, truck: Truck) -> uuid.UUID:
    """Who has to pay to unlock contact info for this match. Config-driven
    (see core/config.py's PAYMENT_CHARGE_PARTY) so pricing/who-pays can be
    tuned without touching code -- just redeploy with a new env var."""
    if settings.payment_charge_party == "shipper":
        return load.user_id
    return truck.user_id  # default: carrier pays (the common real-world dynamic -- carriers pay for job leads)


async def is_contact_unlocked(db: AsyncSession, load: Load, truck: Truck, match: Match) -> bool:
    """
    The single source of truth api/admin.py's "contact" action checks
    before revealing phone numbers. Contact is unlocked when ANY of:
      - payments are disabled entirely (Phase 1 launch default), or
      - this shipper/carrier pair is already a TrustedPair (they paid
        once before, under admin supervision -- re-charging a known,
        vetted relationship on every repeat match would be exactly the
        friction TrustedPair exists to remove), or
      - a Payment for this match exists and is CONFIRMED.
    """
    if not settings.payments_enabled:
        return True
    if await is_trusted_pair(db, load.user_id, truck.user_id):
        return True
    result = await db.execute(
        select(Payment).where(Payment.match_id == match.id, Payment.status == PaymentStatus.CONFIRMED)
    )
    return result.scalar_one_or_none() is not None


async def get_or_create_pending_payment(db: AsyncSession, load: Load, truck: Truck, match: Match) -> Payment:
    """
    Lazily creates the Payment row the first time an admin tries to
    reveal contact for a match that requires one -- there's no need to
    create it earlier (e.g. at match-creation time) since most matches
    the admin looks at might never reach the "contact" step at all
    (rejected, expired, etc.), and a row nobody will ever act on is just
    clutter. Idempotent: a second call for the same match returns the
    existing row rather than erroring or duplicating (see models.Payment's
    UNIQUE(match_id)).
    """
    existing = await db.execute(select(Payment).where(Payment.match_id == match.id))
    row = existing.scalar_one_or_none()
    if row is not None:
        return row

    payment = Payment(
        match_id=match.id,
        payer_user_id=determine_payer_user_id(load, truck),
        amount=settings.payment_contact_fee_amount,
        currency=settings.payment_currency,
    )
    try:
        async with db.begin_nested():
            db.add(payment)
            await db.flush()
        return payment
    except IntegrityError:
        await db.rollback()
        existing = await db.execute(select(Payment).where(Payment.match_id == match.id))
        return existing.scalar_one()


async def get_payment_for_match(db: AsyncSession, match_id: uuid.UUID) -> Payment | None:
    result = await db.execute(select(Payment).where(Payment.match_id == match_id))
    return result.scalar_one_or_none()


async def confirm_payment_manual(db: AsyncSession, payment: Payment, admin_id: uuid.UUID) -> Payment:
    """
    Phase 1 confirmation path: the admin has verified, outside the bot
    (bank transfer receipt, Elcart card-to-card confirmation, cash
    handed over), that the payer's money actually arrived, and taps
    "confirm_payment" in Telegram (see api/admin.py). This is the only
    way a Payment becomes CONFIRMED in Phase 1 -- there is no
    self-service "I paid" button that unlocks anything by itself,
    because that would let a payer claim payment without having
    actually paid.

    Idempotent: confirming an already-CONFIRMED payment just returns it
    unchanged rather than erroring, so a double-tap in the admin UI is
    harmless.
    """
    if payment.status == PaymentStatus.CONFIRMED:
        return payment
    payment.status = PaymentStatus.CONFIRMED
    payment.confirmed_by_admin_id = admin_id
    payment.confirmed_at = datetime.now(timezone.utc)
    return payment

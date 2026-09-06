"""
Notification creation + delivery. Idempotent by design: the
(user_id, load_id, notification_type) unique constraint on the
`notifications` table (see models.py) is the actual guarantee against
duplicates -- not an in-memory check -- so this is safe even if called
concurrently or after a process restart/retry.

Never reveals phone numbers or other contact details in a regular
MATCH_FOUND/BROADCAST/REMINDER/PROACTIVE_MATCH notification. The ONE
exception is AUTO_CONNECTED (see notify_auto_connected below), sent only
when the two parties are already a TrustedPair -- i.e. they've already
exchanged contact info once, under admin supervision -- so it isn't a
new exposure. Otherwise, contact info is only ever shown through the
admin panel's audited "contact" action (api/admin.py).
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import (
    Load, LoadStatus, Match, MatchStatus, Notification, NotificationStatus,
    NotificationType, Truck, User, UserRole,
)
from app.services import telegram_client
from app.services.audit import log_action
from app.services.quotas import is_premium_tier
from app.services.trusted_pairs import is_trusted_pair

logger = logging.getLogger("freightai")


async def _create_notification_row(
    db: AsyncSession, user_id: uuid.UUID, load_id: uuid.UUID, notification_type: NotificationType,
    truck_id: uuid.UUID | None = None, match_id: uuid.UUID | None = None,
) -> tuple[Notification, bool]:
    existing = await db.execute(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.load_id == load_id,
            Notification.notification_type == notification_type,
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        return row, False

    notif = Notification(
        user_id=user_id, load_id=load_id, truck_id=truck_id, match_id=match_id,
        notification_type=notification_type, status=NotificationStatus.SENT,
    )
    try:
        async with db.begin_nested():
            db.add(notif)
            await db.flush()
        return notif, True
    except IntegrityError:
        await db.rollback()
        existing = await db.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.load_id == load_id,
                Notification.notification_type == notification_type,
            )
        )
        return existing.scalar_one(), False


async def _deliver(db: AsyncSession, notif: Notification, chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    ok = await telegram_client.send_message(chat_id, text, reply_markup)
    notif.status = NotificationStatus.SENT if ok else NotificationStatus.FAILED
    if not ok:
        logger.error("notification delivery failed: notif_id=%s user=%s type=%s", notif.id, notif.user_id, notif.notification_type)


def _interest_button(load_id: uuid.UUID) -> dict:
    return {"inline_keyboard": [[{"text": "🚛 Меня интересует", "callback_data": f"interest:{load_id}"}]]}


async def notify_match_found(db: AsyncSession, load: Load, truck: Truck, match: Match, admin_id: uuid.UUID | None) -> None:
    """Sends the MATCH_FOUND notice to shipper, carrier, and admin. Only
    the admin message includes IDs / mediation buttons -- the two
    parties never see each other's contact info."""
    shipper_notif, created_s = await _create_notification_row(
        db, load.user_id, load.id, NotificationType.MATCH_FOUND, truck_id=truck.id, match_id=match.id
    )
    if created_s:
        shipper = await db.get(User, load.user_id)
        text = (
            "📦 Найден подходящий перевозчик для вашего груза\n\n"
            f"🚛 Машина доступна из города {truck.current_city}\n"
            "С вами свяжется администрация платформы для согласования деталей."
        )
        await _deliver(db, shipper_notif, shipper.telegram_id, text)

    carrier_notif, created_c = await _create_notification_row(
        db, truck.user_id, load.id, NotificationType.MATCH_FOUND, truck_id=truck.id, match_id=match.id
    )
    if created_c:
        carrier = await db.get(User, truck.user_id)
        text = (
            "🚛 Найден подходящий груз для вашей машины\n\n"
            f"📍 Погрузка: {load.origin_city}\n"
            f"📍 Разгрузка: {load.destination_city}\n"
            f"📦 Груз: {load.truck_count} машин(ы) ({load.truck_type or 'тип не указан'})\n\n"
            "С вами свяжется администрация платформы для согласования деталей."
        )
        await _deliver(db, carrier_notif, carrier.telegram_id, text)

    if admin_id:
        admin_notif, created_a = await _create_notification_row(
            db, admin_id, load.id, NotificationType.MATCH_FOUND, truck_id=truck.id, match_id=match.id
        )
        if created_a:
            admin = await db.get(User, admin_id)
            text = (
                "🔔 Новое совпадение (MATCH)\n\n"
                f"Груз: {load.origin_city} → {load.destination_city}\n"
                f"Количество: {load.truck_count}\n"
                f"Перевозчик: доступен в {truck.current_city}\n\n"
                f"Load ID: #{str(load.id)[:8]}\n"
                f"Truck/Carrier ID: #{str(truck.id)[:8]}\n\n"
                "Свяжитесь с обеими сторонами и завершите связку через /admin."
            )
            await _deliver(db, admin_notif, admin.telegram_id, text)


async def notify_no_match_yet(db: AsyncSession, load: Load) -> None:
    notif, created = await _create_notification_row(db, load.user_id, load.id, NotificationType.BROADCAST)
    if created:
        shipper = await db.get(User, load.user_id)
        text = "⏳ Пока не нашли подходящего перевозчика.\n\nМы продолжаем поиск и сообщим вам, как только появится подходящая машина."
        await _deliver(db, notif, shipper.telegram_id, text)


async def broadcast_load_to_carrier(db: AsyncSession, load: Load, carrier_user: User) -> Notification | None:
    notif, created = await _create_notification_row(db, carrier_user.id, load.id, NotificationType.BROADCAST)
    if not created:
        return None
    text = (
        "🔔 Новый груз\n\n"
        f"📍 Откуда: {load.origin_city}\n"
        f"📍 Куда: {load.destination_city}\n"
        f"🚚 Требуется: {load.truck_count} ({load.truck_type or 'любой тип'})\n\n"
        "Если вы свободны для этого рейса, нажмите кнопку ниже."
    )
    await _deliver(db, notif, carrier_user.telegram_id, text, reply_markup=_interest_button(load.id))
    return notif


async def send_reminder(db: AsyncSession, load: Load, carrier_user: User, notif: Notification) -> None:
    if notif.reminder_sent or notif.status != NotificationStatus.SENT:
        return
    text = (
        "🔔 Напоминание: груз ждёт перевозчика\n\n"
        f"📍 Откуда: {load.origin_city}\n"
        f"📍 Куда: {load.destination_city}\n\n"
        "Если вы свободны, нажмите кнопку ниже."
    )
    ok = await telegram_client.send_message(carrier_user.telegram_id, text, reply_markup=_interest_button(load.id))
    if ok:
        notif.reminder_sent = True
        from datetime import datetime, timezone
        notif.last_sent_at = datetime.now(timezone.utc)


async def notify_interest(db: AsyncSession, load: Load, truck: Truck, admin_id: uuid.UUID | None) -> None:
    shipper_notif, created_s = await _create_notification_row(
        db, load.user_id, load.id, NotificationType.INTEREST_RECEIVED, truck_id=truck.id
    )
    if created_s:
        shipper = await db.get(User, load.user_id)
        text = (
            "🚛 Один из перевозчиков заинтересовался вашим грузом\n\n"
            "С вами свяжется администрация платформы для согласования деталей."
        )
        await _deliver(db, shipper_notif, shipper.telegram_id, text)

    if admin_id:
        admin_notif, created_a = await _create_notification_row(
            db, admin_id, load.id, NotificationType.INTEREST_RECEIVED, truck_id=truck.id
        )
        if created_a:
            admin = await db.get(User, admin_id)
            text = (
                f"🔔 Перевозчик заинтересован в грузе #{str(load.id)[:8]}\n\n"
                f"{load.origin_city} → {load.destination_city}\n"
                f"Carrier/Truck: #{str(truck.id)[:8]}\n\n"
                "Продолжите через /admin."
            )
            await _deliver(db, admin_notif, admin.telegram_id, text)


async def notify_proactive_match(db: AsyncSession, load: Load, truck: Truck, match: Match, admin_id: uuid.UUID | None) -> None:
    notif, created = await _create_notification_row(
        db, load.user_id, load.id, NotificationType.PROACTIVE_MATCH, truck_id=truck.id, match_id=match.id
    )
    if created:
        shipper = await db.get(User, load.user_id)
        text = (
            f"🔔 Ожидается перевозчик в городе {truck.trip_destination}\n\n"
            "🚛 Машина в пути и может подойти для вашего груза.\n"
            f"📦 Возможно, подойдёт для вашего груза из {load.origin_city} в {load.destination_city}.\n\n"
            "С вами свяжется администрация платформы."
        )
        await _deliver(db, notif, shipper.telegram_id, text)

    carrier_notif, created_c = await _create_notification_row(
        db, truck.user_id, load.id, NotificationType.PROACTIVE_MATCH, truck_id=truck.id, match_id=match.id
    )
    if created_c:
        carrier = await db.get(User, truck.user_id)
        text = (
            "🔔 Возможность обратного груза (Backhaul)\n\n"
            f"📍 Откуда: {load.origin_city}\n📍 Куда: {load.destination_city}\n\n"
            "С вами свяжется администрация платформы, если это вам подходит."
        )
        await _deliver(db, carrier_notif, carrier.telegram_id, text)

    if admin_id:
        admin_notif, created_a = await _create_notification_row(
            db, admin_id, load.id, NotificationType.PROACTIVE_MATCH, truck_id=truck.id, match_id=match.id
        )
        if created_a:
            admin = await db.get(User, admin_id)
            text = (
                f"🔔 Proactive Match\n\nLoad #{str(load.id)[:8]}: {load.origin_city} → {load.destination_city}\n"
                f"Truck #{str(truck.id)[:8]} ETA → {truck.trip_destination}\n\nПроверьте /admin."
            )
            await _deliver(db, admin_notif, admin.telegram_id, text)


async def notify_auto_connected(db: AsyncSession, load: Load, truck: Truck, match: Match, admin_id: uuid.UUID | None) -> None:
    shipper_notif, created_s = await _create_notification_row(
        db, load.user_id, load.id, NotificationType.AUTO_CONNECTED, truck_id=truck.id, match_id=match.id
    )
    if created_s:
        shipper = await db.get(User, load.user_id)
        carrier = await db.get(User, truck.user_id)
        text = (
            "🤝 Совпадение с перевозчиком, с которым вы уже работали\n\n"
            f"📍 Погрузка: {load.origin_city}\n📍 Разгрузка: {load.destination_city}\n\n"
            f"🚛 {carrier.name or 'Перевозчик'} — {carrier.phone or 'номер не указан'}\n\n"
            "Связка выполнена автоматически, так как вы уже сотрудничали через платформу."
        )
        await _deliver(db, shipper_notif, shipper.telegram_id, text)

    carrier_notif, created_c = await _create_notification_row(
        db, truck.user_id, load.id, NotificationType.AUTO_CONNECTED, truck_id=truck.id, match_id=match.id
    )
    if created_c:
        carrier = await db.get(User, truck.user_id)
        shipper = await db.get(User, load.user_id)
        text = (
            "🤝 Совпадение с грузовладельцем, с которым вы уже работали\n\n"
            f"📍 Погрузка: {load.origin_city}\n📍 Разгрузка: {load.destination_city}\n\n"
            f"📦 {shipper.name or 'Грузовладелец'} — {shipper.phone or 'номер не указан'}\n\n"
            "Связка выполнена автоматически, так как вы уже сотрудничали через платформу."
        )
        await _deliver(db, carrier_notif, carrier.telegram_id, text)

    if admin_id:
        admin_notif, created_a = await _create_notification_row(
            db, admin_id, load.id, NotificationType.AUTO_CONNECTED, truck_id=truck.id, match_id=match.id
        )
        if created_a:
            admin = await db.get(User, admin_id)
            text = (
                "🤝 Автоматическая связка (обе стороны уже проверены ранее)\n\n"
                f"Load #{str(load.id)[:8]} ↔ Truck #{str(truck.id)[:8]}\n"
                f"{load.origin_city} → {load.destination_city}\n\n"
                "Действий не требуется — связка выполнена автоматически, так как эти пользователи уже сотрудничали ранее."
            )
            await _deliver(db, admin_notif, admin.telegram_id, text)

    await log_action(
        db, None, "auto_connected_trusted_pair", "match", match.id,
        {"load_id": str(load.id), "truck_id": str(truck.id)},
    )


async def should_skip_admin_mediation(db: AsyncSession, load: Load, truck: Truck) -> bool:
    """
    Single source of truth for "does this match bypass admin review and
    reveal contact info immediately." True when EITHER:
      - this shipper/carrier pair is an established TrustedPair (they
        already went through admin review once, see trusted_pairs.py), or
      - settings.auto_connect_premium_enabled is on AND the carrier is a
        premium-tier user (paid subscriber or has ≥1 referral -- see
        services/quotas.is_premium_tier). Dormant unless explicitly
        turned on; when it is, this is a deliberate trade -- that
        carrier's matches skip the admin's fraud/quality screening in
        exchange for speed, see README.md's "Premium auto-connect"
        section for the full trade-off.

    Deliberately NOT used for proactive/backhaul matches (see
    notify_proactive_match, which never calls this) -- those carry more
    inherent uncertainty (approximate ETA windows) and stay
    admin-mediated regardless of tier or trusted-pair status.
    """
    if await is_trusted_pair(db, load.user_id, truck.user_id):
        return True
    if settings.auto_connect_premium_enabled:
        carrier = await db.get(User, truck.user_id)
        if carrier is not None and is_premium_tier(carrier):
            return True
    return False


async def process_new_match(db: AsyncSession, load: Load, truck: Truck, match: Match, admin_id: uuid.UUID | None) -> None:
    if await should_skip_admin_mediation(db, load, truck):
        match.status = MatchStatus.CONNECTED
        load.status = LoadStatus.CONNECTED
        await notify_auto_connected(db, load, truck, match, admin_id)
    else:
        await notify_match_found(db, load, truck, match, admin_id)

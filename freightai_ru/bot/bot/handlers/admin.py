"""
Admin panel, entirely from Telegram. Every action here calls the
backend's /internal/admin/* endpoints, which re-verify server-side that
the caller's telegram_id resolves to role==ADMIN -- this handler does
NOT itself decide who is an admin (see backend/app/api/admin.py).

The panel is reachable two ways: the /admin command, and a one-tap
"🛠 Панель управления" button shown to admin accounts on /start (see
handlers/start.py's ADMIN_WELCOME + keyboards.ADMIN_PANEL_KB) -- both
render through the same _send_admin_panel() so they can never drift
out of sync.
"""
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from bot.api_client import BackendError, api
from bot.keyboards import admin_match_kb, payment_pending_kb

router = Router(name="admin")


async def _send_admin_panel(target, telegram_id: str) -> None:
    """`target` is anything with an async .answer(text, reply_markup=None)
    method -- a Message (from the /admin command) or a CallbackQuery's
    .message (from the panel button)."""
    try:
        overview = await api.admin_overview(telegram_id)
    except BackendError as e:
        if e.status_code == 403:
            await target.answer("Эта команда доступна только администрации.")
        else:
            await target.answer("Не удалось загрузить панель управления, попробуйте позже.")
        return

    text = (
        "🛠 Панель управления\n\n"
        f"📦 Открытые грузы: {overview['open_loads']}\n"
        f"🚛 Доступные машины: {overview['available_trucks']}\n"
        f"🔔 Новые совпадения: {overview['new_matches']}\n"
        f"⏳ Ожидают совпадения: {overview['waiting_for_match']}\n"
    )
    await target.answer(text)

    matches = await api.admin_matches(telegram_id)
    if not matches:
        await target.answer("Нет новых совпадений, требующих внимания.")
        return

    for m in matches[:10]:
        load, truck = m["load"], m["truck"]
        text = (
            f"🔔 Match #{m['match_id'][:8]} (score={m['score']})\n"
            f"{load['origin']} → {load['destination']}\n"
            f"Перевозчик в: {truck['current_city']}"
        )
        await target.answer(text, reply_markup=admin_match_kb(m["match_id"]))


@router.message(Command("admin"))
async def admin_panel(message: Message):
    await _send_admin_panel(message, str(message.from_user.id))


@router.callback_query(F.data == "adminpanel")
async def admin_panel_button(callback: CallbackQuery):
    await _send_admin_panel(callback.message, str(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:"))
async def admin_action(callback: CallbackQuery):
    _, action, match_id = callback.data.split(":", 2)
    telegram_id = str(callback.from_user.id)
    try:
        result = await api.admin_action(telegram_id, action, match_id=match_id)
    except BackendError as e:
        await callback.answer(e.detail, show_alert=True)
        return

    if action == "contact":
        if result.get("payment_required"):
            payer = result["payer"]
            amount_display = f"{result['amount'] / 100:.2f} {result['currency']}"
            text = (
                "💰 Для этого совпадения требуется оплата перед раскрытием контактов\n\n"
                f"Сумма: {amount_display}\n"
                f"Плательщик: {payer.get('name') or '-'} "
                f"(telegram_id: {payer.get('telegram_id')})\n\n"
                "Свяжитесь с плательщиком, получите оплату вне бота (перевод, Elcart, наличные), "
                "и нажмите кнопку ниже только после того, как деньги действительно поступили."
            )
            await callback.message.answer(text, reply_markup=payment_pending_kb(match_id))
        else:
            shipper, carrier = result["shipper"], result["carrier"]
            text = (
                "📞 Контактные данные\n\n"
                f"Грузовладелец: {shipper.get('name') or '-'} — {shipper.get('phone') or 'номер не указан'}\n"
                f"Перевозчик: {carrier.get('name') or '-'} — {carrier.get('phone') or 'номер не указан'}"
            )
            await callback.message.answer(text)
    elif action == "confirm_payment":
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("✅ Оплата подтверждена. Нажмите «📞 Связаться» ещё раз, чтобы увидеть контакты.")
    elif action == "connect":
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("✅ Статус обновлён: связано.")
    elif action == "reject":
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("❌ Это совпадение отклонено.")
    await callback.answer()


@router.message(Command("subscribe"))
async def grant_subscription(message: Message, command: CommandObject):
    """
    /subscribe <telegram_id> [days]

    Manually activates a carrier's paid subscription (Phase 1: no
    payment gateway wired up yet -- this is the admin confirming payment
    was collected outside the bot, same manual-first pattern as
    Payment). Omit [days] for an indefinite subscription with no expiry.
    Server-side re-checks the caller is actually an admin -- this
    command does nothing for a non-admin caller beyond a 403 message.
    """
    admin_telegram_id = str(message.from_user.id)
    args = (command.args or "").split()
    if not args:
        await message.answer("Использование: /subscribe <telegram_id> [дней]")
        return

    target_telegram_id = args[0]
    days = None
    if len(args) > 1:
        try:
            days = int(args[1])
        except ValueError:
            await message.answer("Количество дней должно быть числом.")
            return

    try:
        result = await api.admin_action(
            admin_telegram_id, "grant_subscription",
            target_telegram_id=target_telegram_id, subscription_days=days,
        )
    except BackendError as e:
        if e.status_code == 403:
            await message.answer("Эта команда доступна только администрации.")
        else:
            await message.answer(f"Не удалось активировать подписку: {e.detail}")
        return

    expiry = result.get("expires_at")
    text = (
        f"💎 Подписка активирована для telegram_id={result['telegram_id']}\n"
        + (f"Действует до: {expiry}" if expiry else "Без срока действия (бессрочно).")
    )
    await message.answer(text)

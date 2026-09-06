from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

ROLE_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🚛 Я перевозчик (есть машина)", callback_data="role:CARRIER")],
    [InlineKeyboardButton(text="📦 Я грузовладелец (есть груз)", callback_data="role:SHIPPER")],
])

YES_NO_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Да", callback_data="yn:yes"), InlineKeyboardButton(text="Нет", callback_data="yn:no")],
])

CONFIRM_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm:yes")],
    [InlineKeyboardButton(text="✏️ Изменить", callback_data="confirm:edit")],
    [InlineKeyboardButton(text="❌ Отмена", callback_data="confirm:cancel")],
])

MANUAL_TYPE_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🚛 У меня есть машина", callback_data="manual:truck")],
    [InlineKeyboardButton(text="📦 У меня есть груз", callback_data="manual:load")],
])

# Shown to ADMIN accounts on /start -- gives them one-tap access to the
# admin panel instead of having to remember/type the /admin command.
ADMIN_PANEL_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🛠 Панель управления", callback_data="adminpanel")],
])


def interest_kb(load_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚛 Меня интересует", callback_data=f"interest:{load_id}")],
    ])


def admin_match_kb(match_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📞 Связаться", callback_data=f"admin:contact:{match_id}"),
            InlineKeyboardButton(text="✅ Связано", callback_data=f"admin:connect:{match_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin:reject:{match_id}"),
        ],
    ])


def payment_pending_kb(match_id: str) -> InlineKeyboardMarkup:
    """Shown to the admin instead of contact info when Phase 2 payments
    are enabled and this match's fee hasn't been confirmed yet -- see
    backend/app/api/admin.py's "contact" action and
    backend/app/services/payments.py."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оплата подтверждена", callback_data=f"admin:confirm_payment:{match_id}")],
    ])


REMOVE_KB = ReplyKeyboardRemove()

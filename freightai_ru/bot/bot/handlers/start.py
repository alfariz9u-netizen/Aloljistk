from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api_client import BackendError, api
from bot.keyboards import ADMIN_PANEL_KB, ROLE_KB
from bot.states import Onboarding

router = Router(name="start")

WELCOME = (
    "Добро пожаловать на платформу поиска перевозчиков и грузов 🚛📦\n\n"
    "Выберите тип вашего аккаунта:"
)

ADMIN_WELCOME = (
    "Добро пожаловать 👋\n\n"
    "Этот аккаунт зарегистрирован как аккаунт администратора платформы.\n"
    "Нажмите кнопку ниже, чтобы открыть панель управления в любое время.\n\n"
    "Аккаунты администратора не могут регистрироваться как перевозчик или "
    "грузовладелец на том же аккаунте — чтобы протестировать систему как "
    "обычный пользователь, используйте другой аккаунт Telegram."
)

ROLE_LABELS = {"CARRIER": "🚛 Перевозчик", "SHIPPER": "📦 Грузовладелец"}


def _parse_referral_code(command: CommandObject, own_telegram_id: str) -> str | None:
    if not command.args or not command.args.startswith("ref_"):
        return None
    candidate = command.args[len("ref_"):]
    if not candidate.isdigit() or candidate == own_telegram_id:
        return None
    return candidate


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    telegram_id = str(message.from_user.id)
    referral_code = _parse_referral_code(command, telegram_id)
    user = await api.upsert_user(telegram_id, message.from_user.full_name, referral_code=referral_code)

    if user["role"] == "ADMIN":
        await message.answer(ADMIN_WELCOME, reply_markup=ADMIN_PANEL_KB)
        return

    if user["role"] in ("CARRIER", "SHIPPER"):
        await message.answer(
            f"С возвращением! Вы зарегистрированы как {ROLE_LABELS[user['role']]}.\n\n"
            "Отправьте описание вашего запроса свободным текстом, например:\n"
            "«Моя машина в Бишкеке, ищу груз до Оша»\n"
            "или «Есть груз, 3 машины, из Бишкека в Ош сегодня»"
        )
        await state.set_state(Onboarding.awaiting_request)
        return

    await message.answer(WELCOME, reply_markup=ROLE_KB)


@router.callback_query(F.data.startswith("role:"))
async def choose_role(callback: CallbackQuery, state: FSMContext):
    role = callback.data.split(":", 1)[1]
    telegram_id = str(callback.from_user.id)
    try:
        await api.set_role(telegram_id, role)
    except BackendError as e:
        await callback.message.edit_text(f"Не удалось сохранить тип аккаунта: {e.detail}")
        await callback.answer()
        return

    await state.update_data(role=role)
    text = f"Выбрано: {ROLE_LABELS[role]}"
    if role == "CARRIER":
        text += (
            "\n\n🎁 Вам предоставлена бесплатная подписка на 30 дней: "
            "неограниченные уведомления о новых грузах и приоритет в рассылках."
        )
    text += "\n\nКак вас зовут?"
    await callback.message.edit_text(text)
    await state.set_state(Onboarding.name)
    await callback.answer()


@router.message(Onboarding.name)
async def got_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()[:120]
    if not name:
        await message.answer("Пожалуйста, введите корректное имя.")
        return
    await state.update_data(name=name)
    await api.upsert_user(str(message.from_user.id), name)
    await message.answer("Ваш номер телефона? (будет использован только администрацией платформы для связи и не будет показан другой стороне)")
    await state.set_state(Onboarding.phone)


@router.message(Onboarding.phone)
async def got_phone(message: Message, state: FSMContext):
    phone = (message.text or "").strip()[:30]
    if not phone:
        await message.answer("Пожалуйста, введите корректный номер телефона.")
        return
    await state.update_data(phone=phone)
    await api.upsert_user(str(message.from_user.id), None, phone)
    data = await state.get_data()
    role = data.get("role")
    example = (
        "«Моя машина в Бишкеке, ищу груз до Оша»" if role == "CARRIER"
        else "«Есть груз, 3 машины, из Бишкека в Ош сегодня»"
    )
    await message.answer(f"Отлично! Теперь опишите ваш запрос свободным текстом, например:\n{example}")
    await state.set_state(Onboarding.awaiting_request)


@router.message(Command("invite"))
async def invite(message: Message):
    telegram_id = str(message.from_user.id)
    try:
        user = await api.get_user(telegram_id)
    except BackendError:
        user = None
    if user is None:
        await message.answer("Сначала зарегистрируйтесь с помощью /start")
        return
    if user["role"] != "CARRIER":
        await message.answer("Реферальная программа доступна только для перевозчиков.")
        return

    bot_username = (await message.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{telegram_id}"

    subscription_line = ""
    if user.get("subscription_active"):
        expires = user.get("subscription_expires_at")
        if expires:
            date_part = expires.split("T")[0]
            subscription_line = f"\n💎 Подписка активна до {date_part} (безлимитные уведомления)."
        else:
            subscription_line = "\n💎 У вас активна безлимитная подписка (бессрочно)."

    text = (
        "🤝 Ваша реферальная ссылка\n\n"
        f"{link}\n\n"
        "Пригласите другого перевозчика по этой ссылке. Как только он "
        "завершит регистрацию (укажет имя и номер телефона), вы получите "
        "+5 к дневному лимиту уведомлений о новых грузах — навсегда.\n\n"
        f"📊 Ваша статистика:\n"
        f"Успешных приглашений: {user['referral_points']}\n"
        f"Дневной лимит уведомлений: {user['daily_broadcast_used']} / {user['daily_quota_limit']}"
        + subscription_line
    )
    await message.answer(text)

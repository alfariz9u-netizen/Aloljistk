"""
Free-text intake -> AI extraction -> user confirmation -> submission, plus
the manual stepwise fallback when extraction fails or the user edits.

Security note: the raw text is sent to the backend's /internal/extract
endpoint, which is the ONLY place it touches the AI. The AI's output
comes back already validated against a strict schema (see
backend/app/schemas/schemas.py ExtractedLoad) -- this handler never lets
AI output skip straight into a create_truck/create_load call without the
user explicitly confirming it first.
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api_client import BackendError, api
from bot.keyboards import CONFIRM_KB, YES_NO_KB, interest_kb
from bot.states import FreeTextConfirm, ManualEntry, Onboarding

logger = logging.getLogger("freightai.bot")
router = Router(name="intake")


def _summary_text(role: str, data: dict) -> str:
    if role == "CARRIER":
        lines = ["Я понял ваш запрос так:\n"]
        lines.append(f"🚛 Текущее местоположение: {data.get('current_city') or '?'}")
        lines.append(f"📍 Желаемое направление: {data.get('desired_destination') or 'любое направление'}")
        lines.append(f"🚚 Тип машины: {data.get('truck_type') or 'не указан'}")
        return "\n".join(lines)
    lines = ["Я понял ваш запрос так:\n"]
    lines.append(f"📍 Погрузка: {data.get('origin_city') or '?'}")
    lines.append(f"📍 Разгрузка: {data.get('destination_city') or '?'}")
    lines.append(f"🚚 Количество: {data.get('truck_count') or 1}")
    lines.append(f"🚚 Тип: {data.get('truck_type') or 'не указан'}")
    return "\n".join(lines)


@router.message(Onboarding.awaiting_request)
async def got_free_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, отправьте текстовое описание вашего запроса.")
        return

    data = await state.get_data()
    role = data.get("role")
    if role not in ("CARRIER", "SHIPPER"):
        user = await api.get_user(str(message.from_user.id))
        role = user["role"] if user else None
    if role not in ("CARRIER", "SHIPPER"):
        await message.answer("Пожалуйста, начните заново с помощью /start")
        return

    try:
        extracted = await api.extract(str(message.from_user.id), text)
    except BackendError:
        extracted = {"type": "unknown", "confidence": 0.0}

    wanted_type = "truck" if role == "CARRIER" else "load"
    if extracted.get("type") != wanted_type or not extracted.get("origin"):
        # Extraction failed / low-confidence / didn't match the user's
        # role -- fall back to manual step-by-step entry rather than
        # guessing.
        await message.answer(
            "Не удалось полностью понять запрос, продолжим по шагам.\n\n"
            + ("В каком городе сейчас находится ваша машина?" if role == "CARRIER" else "Город погрузки?")
        )
        await state.update_data(entry_type=wanted_type, raw_text=text[:1000])
        await state.set_state(ManualEntry.current_city)
        return

    if role == "CARRIER":
        parsed = {
            "current_city": extracted.get("origin"),
            "desired_destination": extracted.get("destination"),
            "truck_type": extracted.get("truck_type"),
            "available": extracted.get("available") if extracted.get("available") is not None else True,
            "raw_text": text[:1000],
        }
    else:
        if not extracted.get("destination"):
            await message.answer("Не удалось понять направление груза, укажите город разгрузки?")
            await state.update_data(entry_type="load", raw_text=text[:1000], origin_city=extracted.get("origin"))
            await state.set_state(ManualEntry.destination)
            return
        parsed = {
            "origin_city": extracted.get("origin"),
            "destination_city": extracted.get("destination"),
            "truck_count": extracted.get("truck_count") or 1,
            "truck_type": extracted.get("truck_type"),
            "loading_time": extracted.get("loading_time"),
            "raw_text": text[:1000],
        }

    await state.update_data(parsed=parsed, role=role)
    await message.answer(_summary_text(role, parsed) + "\n\nДанные верны?", reply_markup=CONFIRM_KB)
    await state.set_state(FreeTextConfirm.reviewing)


async def _submit(message_or_callback, state: FSMContext, telegram_id: str):
    data = await state.get_data()
    role = data.get("role")
    parsed = data.get("parsed", {})
    try:
        if role == "CARRIER":
            payload = {
                "telegram_id": telegram_id,
                "truck_type": parsed.get("truck_type"),
                "current_city": parsed.get("current_city"),
                "desired_destination": parsed.get("desired_destination"),
                "available": parsed.get("available", True),
                "has_current_trip": bool(parsed.get("has_current_trip")),
                "trip_origin": parsed.get("trip_origin"),
                "trip_destination": parsed.get("trip_destination"),
                "trip_eta_minutes_from_now": parsed.get("trip_eta_minutes_from_now"),
            }
            result = await api.create_truck(payload)
            if result.get("match_created"):
                text = "✅ Ваша машина зарегистрирована, мы нашли подходящий груз! С вами свяжется администрация платформы."
            else:
                text = "✅ Ваша машина зарегистрирована. Сообщим вам, как только появится подходящий груз."
        else:
            payload = {
                "telegram_id": telegram_id,
                "origin_city": parsed.get("origin_city"),
                "destination_city": parsed.get("destination_city"),
                "truck_type": parsed.get("truck_type"),
                "truck_count": parsed.get("truck_count") or 1,
                "loading_time": parsed.get("loading_time"),
                "raw_text": parsed.get("raw_text"),
            }
            result = await api.create_load(payload)
            if result.get("match_id"):
                text = "✅ Ваш груз зарегистрирован, мы нашли подходящего перевозчика! С вами свяжется администрация платформы."
            elif result.get("broadcast_to") is not None:
                text = "⏳ Ваш груз зарегистрирован. Пока не нашли подходящего перевозчика, продолжаем поиск и сообщим вам."
            else:
                text = "✅ Ваш груз зарегистрирован."
    except BackendError as e:
        logger.error("submit failed: %s", e.detail)
        text = "Произошла временная ошибка при регистрации запроса, попробуйте ещё раз чуть позже. Если ошибка повторяется, свяжитесь с администрацией."

    await state.set_state(Onboarding.awaiting_request)
    return text


@router.callback_query(FreeTextConfirm.reviewing, F.data == "confirm:yes")
async def confirm_yes(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    text = await _submit(callback, state, str(callback.from_user.id))
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(FreeTextConfirm.reviewing, F.data == "confirm:edit")
async def confirm_edit(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    role = data.get("role")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Хорошо, отправьте описание запроса ещё раз, более подробно.")
    await state.set_state(Onboarding.awaiting_request)
    await state.update_data(role=role)
    await callback.answer()


@router.callback_query(FreeTextConfirm.reviewing, F.data == "confirm:cancel")
async def confirm_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    role = data.get("role")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Отменено.")
    await state.set_state(Onboarding.awaiting_request)
    await state.update_data(role=role)
    await callback.answer()


# --- Manual stepwise fallback ---

@router.message(ManualEntry.current_city)
async def manual_current_city(message: Message, state: FSMContext):
    city = (message.text or "").strip()[:60]
    data = await state.get_data()
    if data.get("entry_type") == "load":
        await state.update_data(origin_city=city)
        await message.answer("Город разгрузки?")
        await state.set_state(ManualEntry.destination)
    else:
        await state.update_data(current_city=city)
        await message.answer("В каком городе/направлении вы ищете груз? (или напишите: любой)")
        await state.set_state(ManualEntry.destination)


@router.message(ManualEntry.destination)
async def manual_destination(message: Message, state: FSMContext):
    dest = (message.text or "").strip()[:60]
    data = await state.get_data()
    if data.get("entry_type") == "load":
        await state.update_data(destination_city=dest)
        await message.answer("Сколько машин требуется? (укажите число)")
        await state.set_state(ManualEntry.truck_count)
    else:
        await state.update_data(desired_destination=None if dest.lower() in ("любой", "любое", "any") else dest)
        await message.answer("Какой тип машины? (например: тент, рефрижератор, самосвал)")
        await state.set_state(ManualEntry.truck_type)


@router.message(ManualEntry.truck_count)
async def manual_truck_count(message: Message, state: FSMContext):
    try:
        count = max(1, min(50, int((message.text or "1").strip())))
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число.")
        return
    await state.update_data(truck_count=count)
    await message.answer("Какой тип машины требуется? (напишите: не важно, если тип не имеет значения)")
    await state.set_state(ManualEntry.truck_type)


@router.message(ManualEntry.truck_type)
async def manual_truck_type(message: Message, state: FSMContext):
    truck_type = (message.text or "").strip()[:60]
    truck_type = None if truck_type.lower() in ("не важно", "-") else truck_type
    data = await state.get_data()
    await state.update_data(truck_type=truck_type)

    if data.get("entry_type") == "load":
        parsed = {
            "origin_city": data.get("origin_city"),
            "destination_city": data.get("destination_city"),
            "truck_count": data.get("truck_count", 1),
            "truck_type": truck_type,
            "loading_time": None,
            "raw_text": data.get("raw_text"),
        }
        await state.update_data(parsed=parsed, role="SHIPPER")
        await message.answer(_summary_text("SHIPPER", parsed) + "\n\nДанные верны?", reply_markup=CONFIRM_KB)
        await state.set_state(FreeTextConfirm.reviewing)
        return

    await message.answer("У вас уже есть текущий рейс (машина с грузом в пути)?", reply_markup=YES_NO_KB)
    await state.set_state(ManualEntry.has_trip)


@router.callback_query(ManualEntry.has_trip, F.data.startswith("yn:"))
async def manual_has_trip(callback: CallbackQuery, state: FSMContext):
    has_trip = callback.data.endswith("yes")
    await state.update_data(has_current_trip=has_trip)
    await callback.message.edit_reply_markup(reply_markup=None)

    if not has_trip:
        data = await state.get_data()
        parsed = {
            "current_city": data.get("current_city"),
            "desired_destination": data.get("desired_destination"),
            "truck_type": data.get("truck_type"),
            "available": True,
            "raw_text": data.get("raw_text"),
        }
        await state.update_data(parsed=parsed, role="CARRIER")
        await callback.message.answer(_summary_text("CARRIER", parsed) + "\n\nДанные верны?", reply_markup=CONFIRM_KB)
        await state.set_state(FreeTextConfirm.reviewing)
        await callback.answer()
        return

    await callback.message.answer("Куда направляется ваш текущий рейс?")
    await state.set_state(ManualEntry.trip_destination)
    await callback.answer()


@router.message(ManualEntry.trip_destination)
async def manual_trip_destination(message: Message, state: FSMContext):
    dest = (message.text or "").strip()[:60]
    await state.update_data(trip_destination=dest)
    await message.answer("Через сколько часов ожидается прибытие? (укажите число, например: 8)")
    await state.set_state(ManualEntry.trip_eta)


@router.message(ManualEntry.trip_eta)
async def manual_trip_eta(message: Message, state: FSMContext):
    try:
        hours = max(0, min(24, float((message.text or "0").strip())))
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число часов.")
        return
    data = await state.get_data()
    parsed = {
        "current_city": data.get("current_city"),
        "desired_destination": data.get("desired_destination"),
        "truck_type": data.get("truck_type"),
        "available": True,
        "has_current_trip": True,
        "trip_origin": data.get("current_city"),
        "trip_destination": data.get("trip_destination"),
        "trip_eta_minutes_from_now": int(hours * 60),
        "raw_text": data.get("raw_text"),
    }
    await state.update_data(parsed=parsed, role="CARRIER")
    await message.answer(_summary_text("CARRIER", parsed) + "\n\nДанные верны?", reply_markup=CONFIRM_KB)
    await state.set_state(FreeTextConfirm.reviewing)


@router.callback_query(F.data.startswith("interest:"))
async def interest_pressed(callback: CallbackQuery):
    load_id = callback.data.split(":", 1)[1]
    telegram_id = str(callback.from_user.id)
    try:
        trucks = await api.my_trucks(telegram_id)
    except BackendError as e:
        await callback.answer(f"Не удалось проверить вашу машину: {e.detail}", show_alert=True)
        return
    if not trucks:
        await callback.answer("У вас нет зарегистрированной машины.", show_alert=True)
        return
    truck_id = trucks[0]["id"]
    try:
        result = await api.register_interest(telegram_id, load_id, truck_id)
    except BackendError as e:
        await callback.answer(f"Не удалось зарегистрировать интерес: {e.detail}", show_alert=True)
        return

    if result.get("registered"):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Ваш интерес зарегистрирован. С вами свяжется администрация платформы для согласования деталей.")
    else:
        await callback.answer("Вы уже зарегистрировали интерес к этому грузу.", show_alert=True)
    await callback.answer()

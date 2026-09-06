"""
Thin, resilient Telegram Bot API client used by the BACKEND to push
notifications (matches, broadcasts, reminders, proactive matches)
independently of the bot's own request/response handlers -- these are
server-initiated pushes, not replies to a user message.

Retries with exponential backoff (1s, 2s, 4s, 8s) on transient failures,
then gives up and lets the caller mark the notification FAILED rather
than blocking or crashing the caller. Never raises past the point where
it would take down the process that queued the notification.
"""
import asyncio
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("freightai")

_BACKOFFS = [1, 2, 4, 8]


async def send_message(chat_id: str, text: str, reply_markup: dict | None = None) -> bool:
    if not settings.telegram_bot_token:
        logger.warning("send_message skipped: no TELEGRAM_BOT_TOKEN configured")
        return False

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt, delay in enumerate([0] + _BACKOFFS):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
                # Don't retry on 4xx caused by bad input (e.g. blocked bot);
                # only retry on 5xx/network-ish failures.
                if 400 <= resp.status_code < 500:
                    logger.warning("Telegram send rejected (chat_id=%s, status=%s)", chat_id, resp.status_code)
                    return False
                last_error = RuntimeError(f"Telegram API {resp.status_code}: {resp.text[:200]}")
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc

    logger.error("Telegram send failed after retries (chat_id=%s): %s", chat_id, last_error)
    return False

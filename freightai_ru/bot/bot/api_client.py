"""
Thin HTTP client the bot uses to talk to the backend. Every call carries
X-Bot-Secret so the backend can verify these requests genuinely come
from our bot process (see backend/app/core/security.py). The bot never
talks to Postgres/Redis for business data directly -- the backend is the
single source of truth and sole place business/security decisions
happen (see project rule 46).
"""
import logging

import httpx

from bot.config import settings

logger = logging.getLogger("freightai.bot")


class BackendError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


class ApiClient:
    def __init__(self):
        self._headers = {"X-Bot-Secret": settings.bot_service_secret}

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{settings.backend_base_url}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.request(method, url, headers=self._headers, **kwargs)
            except httpx.RequestError as exc:
                logger.error("backend request failed: %s %s -- %s", method, path, exc)
                raise BackendError(503, "Сервис временно недоступен") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise BackendError(resp.status_code, detail)
        return resp.json()

    async def upsert_user(self, telegram_id: str, name: str | None, phone: str | None = None, referral_code: str | None = None) -> dict:
        return await self._request(
            "POST", "/internal/users/upsert",
            json={"telegram_id": telegram_id, "name": name, "phone": phone, "referral_code": referral_code},
        )

    async def get_user(self, telegram_id: str) -> dict | None:
        try:
            return await self._request("GET", f"/internal/users/{telegram_id}")
        except BackendError as e:
            if e.status_code == 404:
                return None
            raise

    async def set_role(self, telegram_id: str, role: str) -> dict:
        return await self._request("POST", "/internal/users/role", json={"telegram_id": telegram_id, "role": role})

    async def create_truck(self, payload: dict) -> dict:
        return await self._request("POST", "/internal/trucks", json=payload)

    async def create_load(self, payload: dict) -> dict:
        return await self._request("POST", "/internal/loads", json=payload)

    async def extract(self, telegram_id: str, text: str) -> dict:
        return await self._request("POST", "/internal/extract", json={"telegram_id": telegram_id, "text": text})

    async def register_interest(self, telegram_id: str, load_id: str, truck_id: str) -> dict:
        return await self._request(
            "POST", "/internal/interests",
            json={"telegram_id": telegram_id, "load_id": load_id, "truck_id": truck_id},
        )

    async def my_trucks(self, telegram_id: str) -> list:
        return await self._request("GET", f"/internal/trucks/{telegram_id}")

    async def admin_overview(self, telegram_id: str) -> dict:
        return await self._request("GET", "/internal/admin/overview", params={"telegram_id": telegram_id})

    async def admin_matches(self, telegram_id: str) -> list:
        return await self._request("GET", "/internal/admin/matches", params={"telegram_id": telegram_id})

    async def admin_action(self, telegram_id: str, action: str, match_id: str | None = None, load_id: str | None = None,
                            target_telegram_id: str | None = None, subscription_days: int | None = None) -> dict:
        payload = {"telegram_id": telegram_id, "action": action}
        if match_id:
            payload["match_id"] = match_id
        if load_id:
            payload["load_id"] = load_id
        if target_telegram_id:
            payload["target_telegram_id"] = target_telegram_id
        if subscription_days:
            payload["subscription_days"] = subscription_days
        return await self._request("POST", "/internal/admin/action", json=payload)


api = ApiClient()

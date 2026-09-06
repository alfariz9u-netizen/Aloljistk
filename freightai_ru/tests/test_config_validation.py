"""
Config validation tests for the free single-service (BOT_MODE=webhook)
deployment path -- makes sure a misconfigured production deploy fails
loudly at startup instead of silently accepting unauthenticated webhook/
cron traffic.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _reload_config_with_env(monkeypatch, **env):
    for key in [
        "ENVIRONMENT", "BOT_SERVICE_SECRET", "TELEGRAM_ADMIN_CHAT_ID",
        "BOT_MODE", "PUBLIC_BASE_URL", "TELEGRAM_WEBHOOK_SECRET", "CRON_SECRET",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    if "app.core.config" in sys.modules:
        del sys.modules["app.core.config"]
    return importlib.import_module("app.core.config")


def test_production_requires_cron_secret(monkeypatch):
    with pytest.raises(RuntimeError, match="CRON_SECRET"):
        _reload_config_with_env(
            monkeypatch,
            ENVIRONMENT="production",
            BOT_SERVICE_SECRET="a-real-secret",
            TELEGRAM_ADMIN_CHAT_ID="123",
            BOT_MODE="polling",
        )


def test_production_webhook_mode_requires_public_base_url_and_secret(monkeypatch):
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        _reload_config_with_env(
            monkeypatch,
            ENVIRONMENT="production",
            BOT_SERVICE_SECRET="a-real-secret",
            TELEGRAM_ADMIN_CHAT_ID="123",
            BOT_MODE="webhook",
            CRON_SECRET="a-cron-secret",
        )


def test_production_with_all_required_values_does_not_raise(monkeypatch):
    module = _reload_config_with_env(
        monkeypatch,
        ENVIRONMENT="production",
        BOT_SERVICE_SECRET="a-real-secret",
        TELEGRAM_ADMIN_CHAT_ID="123",
        BOT_MODE="webhook",
        PUBLIC_BASE_URL="https://example.onrender.com",
        TELEGRAM_WEBHOOK_SECRET="a-webhook-secret",
        CRON_SECRET="a-cron-secret",
    )
    assert module.settings.bot_mode == "webhook"


def test_development_mode_does_not_require_any_of_the_above(monkeypatch):
    module = _reload_config_with_env(monkeypatch, ENVIRONMENT="development")
    assert module.settings.environment == "development"

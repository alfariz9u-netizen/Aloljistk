"""
Central configuration. Every secret comes from environment variables --
never hardcode tokens/keys/passwords here. See .env.example.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"

    # Database. Placeholder default lets pure-logic unit tests import
    # services without a real DB / .env present -- SQLAlchemy engines are
    # lazy, so a bogus URL never actually connects until a query runs.
    database_url: str = "postgresql+asyncpg://freight:freight@localhost:5432/freightai_dev"

    redis_url: str = "redis://localhost:6379/0"

    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""

    # Shared secret between the bot process and this backend ONLY. Never
    # exposed to Telegram users. Proves a request genuinely came from our
    # bot process, not an arbitrary client spoofing a telegram_id.
    bot_service_secret: str = "insecure-dev-bot-secret-change-me"

    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"

    # Rate limits
    rate_limit_messages_per_minute: int = 30
    rate_limit_loads_per_hour: int = 10
    rate_limit_trucks_per_hour: int = 10
    rate_limit_ai_per_hour: int = 15

    # Matching windows
    proactive_match_window_minutes: int = 60
    reminder_delay_minutes: int = 10

    # Max characters accepted from a free-text Telegram message before
    # it's rejected outright (cost/DoS safety, independent of rate limits).
    max_free_text_length: int = 1000

    # --- Deployment mode for the Telegram bot ---
    # "polling": the bot runs as its own long-lived process (see
    #   bot/bot/main.py, run via docker-compose's `bot` service). Good
    #   for a VPS/always-on VM where a background process is free to run.
    # "webhook": no separate bot process. This backend itself builds the
    #   aiogram Bot/Dispatcher on startup, registers a Telegram webhook,
    #   and processes updates via the POST /telegram/webhook route (see
    #   main.py). This is what lets the whole app run as a single HTTP
    #   service on a free web-hosting tier that only keeps something
    #   alive when it's receiving requests -- an external ping (or
    #   Telegram itself delivering an update) is enough to keep it warm.
    bot_mode: str = "polling"

    # Public HTTPS base URL this app is reachable at, e.g.
    # https://your-app.onrender.com -- required when bot_mode=webhook,
    # used to register the webhook with Telegram on startup.
    public_base_url: str = ""

    # Secret Telegram is told to send back in the
    # X-Telegram-Bot-Api-Secret-Token header on every webhook call, so
    # /telegram/webhook can reject requests that don't genuinely come
    # from Telegram (anyone can guess the URL, not the secret).
    telegram_webhook_secret: str = ""

    # Guards POST /internal/cron/tick -- the endpoint an external free
    # pinger (e.g. cron-job.org) calls every few minutes to run the
    # reminder + proactive-matching pass instead of a standalone worker
    # process. Deliberately a SEPARATE secret from bot_service_secret:
    # the cron pinger is a less-trusted caller that should only ever be
    # able to trigger this one narrow, idempotent action -- least
    # privilege, not "reuse the bot's full-access secret."
    cron_secret: str = ""

    # --- Payments (Phase 2 monetization, dormant until explicitly turned on) ---
    # Master switch. False = contact reveal is free, exactly like Phase 1
    # launch behavior -- no Payment rows are ever created or checked.
    # Flip to True only once pricing + the manual-confirmation workflow
    # (or, later, an automated gateway) is actually ready to operate.
    payments_enabled: bool = False

    # Who has to pay to unlock contact info for a match: "carrier" or
    # "shipper". ("both" is a documented future extension -- see
    # services/payments.py -- not implemented yet, so only these two
    # values are valid today.)
    payment_charge_party: str = "carrier"

    # Fee in the currency's smallest unit (tyiyn for KGS -- 100 tyiyn =
    # 1 som). Example: 5000 = 50.00 KGS. Stored as an int everywhere
    # (never a float) to avoid rounding bugs with money.
    payment_contact_fee_amount: int = 5000
    payment_currency: str = "KGS"

    # --- Premium auto-connect (dormant by default) ---
    # When True, a match involving a CARRIER who is a paid subscriber
    # or has at least one successful referral skips admin mediation
    # entirely -- contact info is revealed to both sides immediately,
    # the same way an already-TrustedPair match is. This trades away
    # the admin's fraud/quality screening for that match in exchange
    # for speed, as a premium-tier perk -- see README.md's "Premium
    # auto-connect" section for the full trade-off explanation before
    # turning this on. Scoped to direct matches and interest-registered
    # matches only -- proactive/backhaul matches always stay
    # admin-mediated regardless of this setting (see services/notifications.py).
    auto_connect_premium_enabled: bool = False


settings = Settings()

if settings.environment == "production":
    _insecure_defaults = {"bot_service_secret": "insecure-dev-bot-secret-change-me"}
    for _field, _default in _insecure_defaults.items():
        if getattr(settings, _field) == _default:
            raise RuntimeError(
                f"settings.{_field} is still the insecure default. "
                "Set a real secret via environment variable before running in production."
            )
    if not settings.telegram_admin_chat_id:
        raise RuntimeError("TELEGRAM_ADMIN_CHAT_ID must be set in production.")
    if settings.bot_mode == "webhook":
        if not settings.public_base_url:
            raise RuntimeError("PUBLIC_BASE_URL must be set when BOT_MODE=webhook.")
        if not settings.telegram_webhook_secret:
            raise RuntimeError("TELEGRAM_WEBHOOK_SECRET must be set when BOT_MODE=webhook.")
    if not settings.cron_secret:
        raise RuntimeError(
            "CRON_SECRET must be set in production -- it guards POST /internal/cron/tick."
        )
    if settings.payments_enabled and settings.payment_charge_party not in ("carrier", "shipper"):
        raise RuntimeError(
            'PAYMENT_CHARGE_PARTY must be "carrier" or "shipper" when PAYMENTS_ENABLED=true.'
        )

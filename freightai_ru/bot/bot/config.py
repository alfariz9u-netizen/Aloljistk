from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    backend_base_url: str = "http://backend:8000"
    bot_service_secret: str = "insecure-dev-bot-secret-change-me"
    telegram_admin_chat_id: str = ""
    redis_url: str = "redis://redis:6379/0"

    rate_limit_messages_per_minute: int = 30
    max_free_text_length: int = 1000


settings = Settings()

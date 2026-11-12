"""Runtime configuration. Everything comes from environment variables with safe demo defaults."""
import os
from functools import lru_cache


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "postgresql+psycopg://fw:fw@db:5432/fw")
        self.app_base_url = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
        self.session_secret = os.getenv("SESSION_SECRET", "dev-only-change-me")
        self.token_encryption_key = os.getenv("TOKEN_ENCRYPTION_KEY", "")
        self.demo_mode = _bool("DEMO_MODE", True)

        self.payment_provider = os.getenv("PAYMENT_PROVIDER", "mock").strip().lower()
        self.paypal_base_url = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").rstrip("/")
        self.paypal_client_id = os.getenv("PAYPAL_CLIENT_ID", "")
        self.paypal_client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
        self.paypal_vault_enabled = _bool("PAYPAL_VAULT_ENABLED", False)
        self.paypal_payouts_enabled = _bool("PAYPAL_PAYOUTS_ENABLED", False)
        self.paypal_webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "")
        self.demo_hotel_paypal_email = os.getenv("DEMO_HOTEL_PAYPAL_EMAIL", "")

        self.llm_provider = os.getenv("LLM_PROVIDER", "mock").strip().lower()
        self.llm_model = os.getenv("LLM_MODEL", "")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.groq_api_key = os.getenv("GROQ_API_KEY", "")

        # E4: how long participants have to answer a task consent request before the owner's authorization is used.
        self.consent_timeout_seconds = int(os.getenv("CONSENT_TIMEOUT_SECONDS", "300"))
        self.worker_poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", "1.5"))
        self.static_dir = os.getenv("STATIC_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"))

    @property
    def paypal_real(self) -> bool:
        return self.payment_provider == "paypal"


@lru_cache
def get_settings() -> Settings:
    return Settings()

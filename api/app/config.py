from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://wastewise:wastewise@db:5432/wastewise"

    # Auth
    secret_key: str = "insecure-dev-key-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 43200  # 30 days — single-user personal app

    # Seeded first user
    first_user_email: str = "user@example.com"
    first_user_password: str = "change-me"

    # --- LLM: free tiers only, no paid inference anywhere ---
    # Groq is primary. gpt-oss is the family with *strict* constrained decoding, so a JSON
    # schema is guaranteed rather than hoped for.
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    # Gemini takes over when Groq rate-limits (free tier is ~30 RPM / 1K RPD).
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    llm_timeout_s: float = 20.0

    # --- Open Food Facts ---
    # Keyless, but a descriptive User-Agent is mandatory: generic ones get blocked.
    off_base_url: str = "https://world.openfoodfacts.org"
    off_contact_email: str = "wastewise@example.com"
    off_timeout_s: float = 6.0
    # Self-imposed, below OFF's documented ~100/min for product GETs. Every result is
    # cached forever, so steady-state traffic is near zero and this only caps a first shop.
    off_rate_limit_per_min: int = 50

    # App
    tz: str = "Europe/Rome"
    default_currency: str = "EUR"
    cors_origins: str = "http://localhost:5173"

    @property
    def off_user_agent(self) -> str:
        """OFF requires `AppName/Version (contact)`. Without it requests get 403'd."""
        return f"WasteWise/0.1 ({self.off_contact_email})"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

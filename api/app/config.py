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
    # `-latest` rather than a pinned version on purpose: a pinned Gemini model eventually
    # answers 404 "no longer available to new users", which is exactly how the failover
    # silently stopped working here — the pin was fine for the key that created it and dead
    # for a fresh one. A failover nobody exercises is a failover nobody notices breaking.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    llm_timeout_s: float = 20.0

    # --- Open Food Facts ---
    # Keyless, but a descriptive User-Agent is mandatory: generic ones get blocked.
    off_base_url: str = "https://world.openfoodfacts.org"
    off_contact_email: str = "wastewise@example.com"
    off_timeout_s: float = 6.0
    # Self-imposed, below OFF's documented ~100/min for product GETs. Every result is
    # cached forever, so steady-state traffic is near zero and this only caps a first shop.
    off_rate_limit_per_min: int = 50

    # --- Risk model ---
    # Bumped whenever the scoring logic changes, and stamped onto every `risk_scores` row:
    # a score whose model can't be identified can't be evaluated against later outcomes.
    risk_model_version: str = "v0"
    # The v1 survival model only earns its place with enough resolved outcomes to fit on.
    # Below these counts the fit is refused rather than produced-and-caveated — a c-index
    # from 40 items is noise wearing a number's clothes.
    risk_v1_min_resolved: int = 150
    risk_v1_min_waste_events: int = 40
    # P(consumed before expiry) below this flags an item as at risk. It is the operating
    # threshold every precision/recall figure is reported at, so it belongs in config
    # rather than buried in the scorer.
    risk_at_risk_threshold: float = 0.5

    # --- Recipe crawler ---
    # Same courtesy rules as the OFF client, and non-negotiable: identify yourself, obey
    # robots.txt, and stay slow. This corpus is personal and never redistributed.
    crawler_contact_email: str = "wastewise@example.com"
    crawler_delay_s: float = 2.0
    crawler_timeout_s: float = 15.0
    crawler_max_pages_per_host: int = 1500
    # all-MiniLM-L6-v2 at 384 dims, to match the `recipes.embedding` column. Run through
    # onnxruntime rather than torch — same vectors, ~50 MB instead of ~2 GB.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384

    # App
    tz: str = "Europe/Rome"
    default_currency: str = "EUR"
    cors_origins: str = "http://localhost:5173"

    @property
    def off_user_agent(self) -> str:
        """OFF requires `AppName/Version (contact)`. Without it requests get 403'd."""
        return f"WasteWise/0.1 ({self.off_contact_email})"

    @property
    def crawler_user_agent(self) -> str:
        """Identifying, with a contact address, so a site owner who objects can reach us."""
        return f"WasteWiseBot/0.1 (+{self.crawler_contact_email})"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

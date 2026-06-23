from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Anthropic
    anthropic_api_key: str

    # OpenAI - only used as cross-provider fallback when Anthropic is down
    openai_api_key: str = ""

    # LangSmith
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "triage"

    # Database
    database_url: str = "postgresql+psycopg://triage:triage@localhost:5433/triage"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Embeddings
    voyage_api_key: str = ""

    # Model names - change here to swap models across the entire system
    router_model: str = "claude-haiku-4-5-20251001"
    specialist_model: str = "claude-sonnet-4-6"
    quality_checker_model: str = "claude-haiku-4-5-20251001"

    # API
    api_key: str = ""  # set a real key in .env; empty string means auth always passes
    environment: str = "development"  # set to "production" to enforce X-API-Key checks
    redis_stream_name: str = "triage:tickets"
    # When true, POST /tickets runs the pipeline inline and blocks until resolved.
    # Set SYNC_MODE=true in deployed environments where no worker process runs.
    sync_mode: bool = False
    # Override via CORS_ORIGINS='["https://demo.example.com"]' in .env
    cors_origins: list[str] = ["http://localhost:8501"]


settings = Settings()  # type: ignore[call-arg]

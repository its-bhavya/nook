"""
Central config loaded from environment variables.
Never hardcode secrets — read them here and import settings elsewhere.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # GitHub
    github_token: str = ""

    # TinyFish
    tinyfish_api_key: str = ""

    # LLM
    llm_api_key: str = ""
    llm_provider: str = "gemini"  # gemini | openai
    llm_model: str = "gemini-3.8-flash"  # override via LLM_MODEL in .env

    # Feature flags
    demo_mode: bool = True  # When True, bypass live agents and return mock data

    # Discovery fallback: comma-separated URLs to try when GitHub extraction returns nothing
    # e.g. https://goodfirstissue.dev/language/python
    discovery_fallback_urls: str = ""

    # Timeouts (seconds)
    tinyfish_timeout: int = 120       # Max wall-clock seconds per TinyFish run (legacy default)
    investigate_timeout_s: int = 240  # Max wall-clock seconds for an investigation run (INVESTIGATE_TIMEOUT_S)
    discover_timeout_s: int = 270     # was 150
    tinyfish_max_concurrent: int = 4 
    llm_timeout: int = 120  # Total seconds to wait for a Gemini response

    # ---- Neon services (injected by `neon env pull` / `neon deploy`) ----
    # Postgres (Lakebase Postgres) — pooled URL for app traffic, direct for migrations
    database_url: str = ""            # pooled (-pooler) connection string
    database_url_unpooled: str = ""   # direct connection string (migrations, dumps)
    neon_branch: str = ""             # the Neon branch these vars belong to

    # Neon AI Gateway — OpenAI-compatible chat completions endpoint
    ai_gateway_model: str = "gpt-5-mini"  # default catalog model; override via env
    # Injected only when `aiGateway: true` in neon.ts AND the plan can serve it.
    neon_ai_gateway_token: str = ""
    neon_ai_gateway_base_url: str = ""   # bare host, no path

    # Neon Object Storage (S3-compatible) — AWS-standard env vars
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_endpoint_url_s3: str = ""
    aws_region: str = ""
    storage_bucket_name: str = "assets"  # matches the bucket declared in neon.ts

    # `.env.local` is written by Neon and holds the injected service vars;
    # `.env` is for your own secrets (TINYFISH_API_KEY, etc.). Load both.
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

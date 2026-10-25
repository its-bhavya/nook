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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

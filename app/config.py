"""
Central config loaded from environment variables.
Never hardcode secrets — read them here and import settings elsewhere.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # TinyFish
    tinyfish_api_key: str = ""

    # LLM
    llm_api_key: str = ""
    llm_provider: str = "openai"  # e.g. openai | gemini | anthropic

    # Feature flags
    demo_mode: bool = False  # When True, bypass live agents and return mock data

    # Timeouts (seconds)
    tinyfish_timeout: int = 120   # Max wall-clock seconds per TinyFish run
    tinyfish_max_concurrent: int = 3  # Max parallel TinyFish runs
    llm_timeout: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

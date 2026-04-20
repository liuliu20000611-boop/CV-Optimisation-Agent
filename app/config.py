"""Runtime configuration from environment variables only — no secrets in code."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loads DEEPSEEK_* variables from the environment or optional `.env` (local dev)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = Field(
        default="",
        description="DeepSeek API key. Set env DEEPSEEK_API_KEY；未设置时仅健康检查/静态页等可用，调用模型将返回 503。",
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="OpenAI-compatible API base URL.",
    )
    deepseek_model: str = Field(
        default="deepseek-chat",
        description="Chat model name for DeepSeek.",
    )
    max_resume_chars: int = Field(default=120_000, ge=1000, le=500_000)
    max_jd_chars: int = Field(default=50_000, ge=100, le=200_000)
    max_upload_bytes: int = Field(
        default=2_097_152,
        ge=1024,
        le=5_000_000,
        description="Max upload size for resume files (PDF/Word etc.), default 2MiB",
    )
    request_timeout_seconds: float = Field(default=120.0, ge=10.0, le=600.0)

    # Rate limit (in-process; use gateway for multi-replica)
    rate_limit_per_minute: int = Field(default=60, ge=5, le=10_000)
    rate_limit_enabled: bool = Field(default=True)

    # Analysis cache (single worker)
    enable_analysis_cache: bool = Field(default=True)
    analysis_cache_ttl_seconds: int = Field(default=300, ge=30, le=3600)

    # Tests / CI: disable rate limit and optional behaviors
    testing: bool = Field(default=False, description="Set TESTING=1 in pytest")


@lru_cache
def get_settings() -> Settings:
    return Settings()

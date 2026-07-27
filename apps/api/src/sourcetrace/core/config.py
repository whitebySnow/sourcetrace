from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "SourceTrace API"
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    api_request_id_header: str = "X-Request-ID"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://sourcetrace:sourcetrace_dev@localhost:5432/sourcetrace"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: Path = Path("data/uploads")
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default=500, ge=1)
    ingestion_tokenizer: str = "cl100k_base"
    ingestion_chunk_size: int = Field(default=500, gt=0)
    ingestion_chunk_overlap: int = Field(default=80, ge=0)
    ingestion_chunking_config_version: str = "token-window-v1"

    llm_provider: str = "mock"
    llm_model: str = "mock-chat"
    embedding_provider: str = "mock"
    embedding_model: str = "mock-embedding"


@lru_cache
def get_settings() -> Settings:
    return Settings()

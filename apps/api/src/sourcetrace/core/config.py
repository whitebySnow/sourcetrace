from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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

    database_url: str = (
        "postgresql+asyncpg://sourcetrace:sourcetrace_dev@localhost:5432/sourcetrace"
    )
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

    llm_provider: str = "openai-compatible"
    llm_base_url: str = "https://api.example.com/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str = "deepseek-v4-flash"
    llm_connect_timeout_seconds: float = Field(default=10, gt=0)
    llm_read_timeout_seconds: float = Field(default=120, gt=0)
    llm_request_timeout_seconds: float = Field(default=180, gt=0)
    llm_operation_deadline_seconds: float = Field(default=361, gt=0)
    llm_answer_output_thinking: Literal["default", "enabled", "disabled"] = "disabled"
    llm_structured_output_mode: Literal["text", "json_object"] = "json_object"
    llm_structured_output_thinking: Literal["default", "enabled", "disabled"] = "disabled"
    llm_structured_output_max_tokens: int = Field(default=2048, ge=256, le=16384)
    llm_prompt_version: str = "grounded-answer-v6"
    llm_retrieval_plan_prompt_version: str = "two-stage-evidence-slots-v6"
    llm_evidence_assessment_prompt_version: str = "evidence-assessment-v4"
    llm_citation_repair_prompt_version: str = "citation-repair-v7"
    answer_context_question_limit: int = Field(default=4, ge=1, le=20)
    retrieval_top_k: int = Field(default=8, ge=1, le=8)
    retrieval_page_neighbor_count: int = Field(default=1, ge=0, le=3)
    retrieval_rrf_rank_constant: int = Field(default=60, ge=1, le=1000)
    retrieval_minimum_score: float = Field(default=0.5, ge=-1, le=1)
    retrieval_minimum_evidence: int = Field(default=1, ge=1)
    retrieval_config_version: str = "pgvector-hybrid-query-aware-bge-reranker-v7"
    answer_workflow_version: str = "langgraph-bounded-multi-query-v6"
    embedding_provider: str = "sentence-transformers"
    embedding_model: str = "BAAI/bge-m3"
    embedding_model_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    embedding_cache_dir: Path = Path(r"D:\DevelopEnvironment\huggingface")
    embedding_hf_endpoint: str | None = "https://hf-mirror.com"
    embedding_device: str = "cpu"
    embedding_batch_size: int = Field(default=8, ge=1)
    embedding_dimension: int = Field(default=1024, ge=1)
    embedding_config_version: str = "bge-m3-dense-v1"
    reranker_provider: str = "sentence-transformers"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_model_revision: str = "e099d4b9cdbd291b1569d416f19aaa6523570bc3"
    reranker_model_weight_sha256: str = (
        "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286"
    )
    reranker_cache_dir: Path = Path(r"D:\DevelopEnvironment\huggingface")
    reranker_device: str = "cpu"
    reranker_batch_size: int = Field(default=8, ge=1)
    reranker_config_version: str = "bge-reranker-v2-m3-cross-encoder-v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()

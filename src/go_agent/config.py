"""Application settings loaded from environment and optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


class Settings(BaseSettings):
    """Runtime configuration for go-agent."""

    model_config = SettingsConfigDict(
        env_prefix="GO_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    work_dir: Path = Path("./workspaces")
    artifacts_dir: Path = Path("./artifacts")
    log_level: str = "INFO"
    max_fix_iterations: int = 5
    max_issue_comments: int = 20
    repo_map_max_depth: int = 4
    repo_map_skip_vendor: bool = True
    ripgrep_timeout: int = 30
    ripgrep_max_results: int = 50
    ripgrep_default_glob: str = "*.go"
    context_max_chars: int = 80000
    context_max_files: int = 15
    context_graph_max_hops: int = 2
    context_snippet_radius: int = 5
    context_full_file_top_k: int = 3
    context_summary_top_k: int = 5
    enable_rag: bool = False
    rag_top_k: int = 10
    rag_chunk_lines: int = 80
    rag_chunk_overlap: int = 20
    rag_embed_provider: str = "local"
    rag_embed_model: str = "all-MiniLM-L6-v2"
    rag_min_score: float = 0.3
    llm_max_retries: int = 3
    llm_retry_base_delay: float = 1.0

    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    github_token: str | None = Field(default=None, validation_alias="GITHUB_TOKEN")
    model_fast: str = "gpt-4o-mini"
    model_strong: str = "gpt-4o"

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> str:
        if value is None:
            return "INFO"
        level = str(value).strip().upper()
        if level not in _VALID_LOG_LEVELS:
            msg = f"invalid log level {value!r}; use DEBUG, INFO, WARNING, or ERROR"
            raise ValueError(msg)
        return level

    @field_validator("work_dir", "artifacts_dir", mode="before")
    @classmethod
    def coerce_path(cls, value: object) -> Path:
        if isinstance(value, Path):
            return value.expanduser().resolve()
        return Path(str(value)).expanduser().resolve()

    @property
    def logging_level(self) -> int:
        import logging

        return getattr(logging, self.log_level)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()

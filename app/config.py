"""Application configuration.

All secrets must be supplied via environment variables. This module intentionally
avoids printing secret values.
"""

from __future__ import annotations

from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Settings for the worker process."""

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # GitHub auth and repository context
    github_token: str | None = None
    engineer_pat_key: str | None = None
    github_api_base_url: str = "https://api.github.com"

    # Work root. If unset, defaults to /work in containers and ./work locally.
    work_root: str | None = None

    # Optional default context (can be supplied via /event payload)
    repo: str | None = None  # "owner/repo"
    issue_number: int | None = None
    base_branch: str = "main"

    # Provider
    openhands_command: str | None = None
    # Common LLM env vars passed through to OpenHands (if set)
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    gemini_api_key: str | None = None
    google_api_key: str | None = None

    # Git author
    git_author_name: str = "swe-worker-bot"
    git_author_email: str = "swe-worker-bot@example.com"

    # Verification commands (optional)
    verify_commands: str | None = None  # newline-separated shell commands

    # Server
    listen_host: str = "0.0.0.0"
    listen_port: int = 8000

    @field_validator(
        "github_token",
        "engineer_pat_key",
        "openai_api_key",
        "openai_base_url",
        "openai_model",
        "llm_api_key",
        "llm_base_url",
        "llm_model",
        "gemini_api_key",
        "google_api_key",
        mode="before",
    )
    @classmethod
    def _normalize_env_string(cls, value: Any) -> Any:
        """Normalizes env var strings.

        Docker's `--env-file` does not strip quotes. To avoid subtle auth failures
        like 401 caused by surrounding quotes, we trim whitespace and strip a
        single pair of surrounding quotes.
        """

        if value is None or not isinstance(value, str):
            return value
        text = value.strip()
        if len(text) >= 2 and ((text[0] == text[-1] == '"') or (text[0] == text[-1] == "'")):
            text = text[1:-1].strip()
        return text or None

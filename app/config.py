"""Application configuration.

All secrets must be supplied via environment variables. This module intentionally
avoids printing secret values.
"""

from __future__ import annotations

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

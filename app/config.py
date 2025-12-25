"""Application configuration.

All secrets must be supplied via environment variables. This module intentionally
avoids printing secret values.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Settings for the worker process."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # Persistence
    data_dir: str = "/data"

    # GitHub auth and repository context
    github_token: str | None = None
    github_api_base_url: str = "https://api.github.com"

    # Optional default context (can be supplied via /event payload)
    repo: str | None = None  # "owner/repo"
    issue_number: int | None = None
    base_branch: str = "main"

    # Provider
    openhands_command: str | None = None

    # Git author
    git_author_name: str = "swe-worker-bot"
    git_author_email: str = "swe-worker-bot@example.com"

    # Verification commands (optional)
    verify_commands: str | None = None  # newline-separated shell commands

    # Server
    listen_host: str = "0.0.0.0"
    listen_port: int = 8000

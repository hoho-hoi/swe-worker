"""No-op provider used when OpenHands is not configured."""

from __future__ import annotations

from app.providers.base import Provider, ProviderResult, Task


class NoOpProvider(Provider):
    """Provider that does nothing and returns failure."""

    def __init__(self, *, message: str) -> None:
        self._message = message

    def run(self, *, task: Task, repo_path: str) -> ProviderResult:
        return ProviderResult(success=False, summary=self._message, log_excerpt=None)

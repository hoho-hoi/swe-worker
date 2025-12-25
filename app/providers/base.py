"""Provider interface for code generation/repair agents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    """Task passed to a provider.

    Attributes:
        repo: Repository in 'owner/repo' format.
        issue_number: Issue number.
        issue_title: Issue title.
        issue_body: Issue body markdown.
        comments_markdown: Concatenated issue comments in markdown.
        constraints_markdown: Additional constraints or policies in markdown.
    """

    repo: str
    issue_number: int
    issue_title: str
    issue_body: str
    comments_markdown: str
    constraints_markdown: str


@dataclass(frozen=True)
class ProviderResult:
    """Result from a provider run."""

    success: bool
    summary: str
    log_excerpt: str | None = None


class Provider:
    """Abstract provider.

    Implementations should modify the repository working tree under ``repo_path``.
    """

    def run(self, *, task: Task, repo_path: str) -> ProviderResult:
        """Runs the provider.

        Args:
            task: The task details.
            repo_path: Path to the checked-out repository.

        Returns:
            ProviderResult describing success and a short summary.
        """

        raise NotImplementedError

"""GitHub REST API wrapper.

This module uses GitHub REST v3 endpoints. Authentication is performed via
``Authorization: Bearer <token>`` header.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field


class GitHubApiError(RuntimeError):
    """Raised when GitHub API returns a non-success response."""

    def __init__(self, *, status_code: int, message: str) -> None:
        super().__init__(f"GitHub API error: status={status_code}, message={message}")
        self.status_code = status_code
        self.message = message


class Issue(BaseModel):
    """Subset of GitHub Issue fields used by the worker."""

    number: int = Field(..., ge=1)
    title: str
    body: str | None = None


class IssueComment(BaseModel):
    """Subset of GitHub Issue Comment fields used by the worker."""

    id: int = Field(..., ge=1)
    body: str | None = None


class PullRequestCreated(BaseModel):
    """Subset of PR creation response fields used by the worker."""

    number: int = Field(..., ge=1)
    html_url: str
    body: str | None = None


class PullRequest(BaseModel):
    """Subset of PR fields used by the worker."""

    number: int = Field(..., ge=1)
    html_url: str
    body: str | None = None


@dataclass(frozen=True)
class GitHubClientConfig:
    """GitHub client configuration."""

    api_base_url: str
    token: str


class GitHubClient:
    """Thin wrapper around GitHub REST API."""

    def __init__(self, *, config: GitHubClientConfig) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=self._config.api_base_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._config.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "swe-worker",
            },
            timeout=httpx.Timeout(30.0),
        )

    def close(self) -> None:
        """Closes underlying HTTP client."""

        self._client.close()

    def get_issue(self, *, repo: str, issue_number: int) -> Issue:
        """Fetches issue details."""

        resp = self._client.get(f"/repos/{repo}/issues/{issue_number}")
        self._raise_for_error(resp)
        return Issue.model_validate(resp.json())

    def list_issue_comments_since(
        self,
        *,
        repo: str,
        issue_number: int,
        last_seen_comment_id: int,
    ) -> list[IssueComment]:
        """Lists issue comments with id > last_seen_comment_id.

        Note:
            GitHub API does not support filtering by comment id directly.
            This method paginates and filters client-side.
        """

        comments: list[IssueComment] = []
        for item in self._paginate(
            f"/repos/{repo}/issues/{issue_number}/comments",
            params={"per_page": "100"},
        ):
            comment = IssueComment.model_validate(item)
            if comment.id > last_seen_comment_id:
                comments.append(comment)
        comments.sort(key=lambda c: c.id)
        return comments

    def create_issue_comment(self, *, repo: str, issue_number: int, body: str) -> int:
        """Creates a comment on an issue."""

        resp = self._client.post(
            f"/repos/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        self._raise_for_error(resp)
        payload = resp.json()
        comment = IssueComment.model_validate(payload)
        return comment.id

    def create_pull_request(
        self,
        *,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> PullRequestCreated:
        """Creates a Ready PR (draft is disabled)."""

        resp = self._client.post(
            f"/repos/{repo}/pulls",
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                "draft": False,
            },
        )
        self._raise_for_error(resp)
        return PullRequestCreated.model_validate(resp.json())

    def update_pull_request_body(self, *, repo: str, pr_number: int, body: str) -> None:
        """Updates PR body."""

        resp = self._client.patch(
            f"/repos/{repo}/pulls/{pr_number}",
            json={"body": body},
        )
        self._raise_for_error(resp)

    def get_pull_request(self, *, repo: str, pr_number: int) -> PullRequest:
        """Fetches a PR."""

        resp = self._client.get(f"/repos/{repo}/pulls/{pr_number}")
        self._raise_for_error(resp)
        return PullRequest.model_validate(resp.json())

    def create_pull_request_comment(self, *, repo: str, pr_number: int, body: str) -> None:
        """Creates an issue-comment on a PR (timeline comment)."""

        resp = self._client.post(
            f"/repos/{repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
        self._raise_for_error(resp)

    def _paginate(self, path: str, *, params: dict[str, str]) -> Iterable[dict[str, object]]:
        next_url: str | None = str(self._client.base_url.join(path))
        current_params = dict(params)
        while next_url is not None:
            resp = self._client.get(next_url, params=current_params)
            self._raise_for_error(resp)
            payload = resp.json()
            if not isinstance(payload, list):
                raise GitHubApiError(
                    status_code=resp.status_code,
                    message="Unexpected payload type for pagination.",
                )
            for item in payload:
                if isinstance(item, dict):
                    yield item
            next_url = self._parse_next_link(resp.headers.get("Link"))
            current_params = {}

    @staticmethod
    def _parse_next_link(link_header: str | None) -> str | None:
        if not link_header:
            return None
        # Example: <https://api.github.com/...page=2>; rel="next", <...>; rel="last"
        parts = [p.strip() for p in link_header.split(",")]
        for part in parts:
            if 'rel="next"' in part:
                left = part.find("<")
                right = part.find(">")
                if left >= 0 and right > left:
                    return part[left + 1 : right]
        return None

    @staticmethod
    def _raise_for_error(resp: httpx.Response) -> None:
        if 200 <= resp.status_code < 300:
            return
        message = resp.text
        raise GitHubApiError(status_code=resp.status_code, message=message)

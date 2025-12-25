"""Engineer loop orchestrating a single event execution.

This module is intentionally synchronous (blocking) and is expected to be run
in a background thread by the HTTP server.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.git_ops import GitCommandError, GitOps
from app.github_client import GitHubApiError, GitHubClient
from app.pr_template import PullRequestBodyInput, PullRequestBodyRenderer
from app.providers.base import Provider, Task
from app.state_store import StateStore, WorkerState


@dataclass(frozen=True)
class WorkerEvent:
    """Incoming event that triggers a run."""

    type: str
    repo: str | None = None
    issue_number: int | None = None
    base_branch: str | None = None


@dataclass(frozen=True)
class RunResult:
    """Result of a single run."""

    success: bool
    message: str


class StopRequestedError(RuntimeError):
    """Raised when the run is cancelled."""


class EngineerLoop:
    """Coordinates fetching context, running provider, and creating/updating PR."""

    _CONSTRAINTS_MARKDOWN = (
        "## Constraints\n"
        "- Use clear English names (verb+object). Avoid abbreviations.\n"
        "- Prefer maintainability, readability, security.\n"
        "- Avoid hardcoded secrets; use environment variables.\n"
        "- Handle errors and edge cases explicitly; no happy-path assumptions.\n"
        "- Add unit tests and a usage example.\n"
    )

    def __init__(
        self,
        *,
        state_store: StateStore,
        github_client: GitHubClient,
        git_ops: GitOps,
        provider: Provider,
        pr_body_renderer: PullRequestBodyRenderer,
        github_token: str,
        verify_commands: list[str] | None = None,
    ) -> None:
        self._state_store = state_store
        self._github_client = github_client
        self._git_ops = git_ops
        self._provider = provider
        self._pr_body_renderer = pr_body_renderer
        self._github_token = github_token
        self._verify_commands = verify_commands or []

    def run(
        self, *, event: WorkerEvent, stop_checker: Callable[[], bool] | None = None
    ) -> RunResult:
        """Runs a single event execution.

        Args:
            event: Worker event.
            stop_checker: Optional callable that returns True when cancellation is requested.

        Returns:
            RunResult with success/failure.
        """

        stop_check: Callable[[], bool] | None = stop_checker if stop_checker is not None else None

        def raise_if_stopped() -> None:
            if stop_check is not None and stop_check():
                raise StopRequestedError("Stop requested.")

        repo, issue_number, base_branch = self._resolve_context(event)
        branch = f"agent/issue-{issue_number}"

        state = self._state_store.load_or_initialize(
            repo=repo,
            issue_number=issue_number,
            base_branch=base_branch,
            branch=branch,
        )

        state.last_run_status = "running"
        state.last_error = None
        self._state_store.save(state)

        try:
            raise_if_stopped()
            issue = self._github_client.get_issue(repo=repo, issue_number=issue_number)
            raise_if_stopped()
            comments = self._github_client.list_issue_comments_since(
                repo=repo,
                issue_number=issue_number,
                last_seen_comment_id=state.last_seen_comment_id,
            )
            max_comment_id = max((c.id for c in comments), default=state.last_seen_comment_id)
            comments_markdown = "\n\n".join(
                f"### Comment {c.id}\n\n{(c.body or '').strip()}" for c in comments
            ).strip()

            self._git_ops.clone_if_needed(
                repo=repo,
                dest_dir=str(self._state_store.paths.repo_dir),
                base_branch=base_branch,
                github_token=self._github_token,
            )
            raise_if_stopped()
            self._git_ops.ensure_branch_checked_out(
                repo_dir=str(self._state_store.paths.repo_dir),
                base_branch=base_branch,
                branch=branch,
                github_token=self._github_token,
            )
            raise_if_stopped()

            provider_task = Task(
                repo=repo,
                issue_number=issue_number,
                issue_title=issue.title,
                issue_body=(issue.body or "").strip(),
                comments_markdown=comments_markdown,
                constraints_markdown=self._CONSTRAINTS_MARKDOWN,
            )
            provider_result = self._provider.run(
                task=provider_task,
                repo_path=str(self._state_store.paths.repo_dir),
            )
            raise_if_stopped()

            verify_output = self._run_verify_commands_or_raise(
                repo_dir=str(self._state_store.paths.repo_dir),
            )
            raise_if_stopped()

            committed_sha = self._git_ops.commit_all_if_dirty(
                repo_dir=str(self._state_store.paths.repo_dir),
                message=self._build_commit_message(
                    issue_number=issue_number, issue_title=issue.title
                ),
            )
            if committed_sha is not None:
                self._git_ops.push_branch(
                    repo_dir=str(self._state_store.paths.repo_dir),
                    branch=branch,
                    github_token=self._github_token,
                )

            head_sha = self._git_ops.get_head_sha(repo_dir=str(self._state_store.paths.repo_dir))
            state.last_head_sha = head_sha

            pr_number = state.pr_number
            pr_url: str | None = None
            pr_body = self._pr_body_renderer.render(
                data=PullRequestBodyInput(
                    issue_number=issue_number,
                    summary=provider_result.summary,
                    how_to_test=verify_output or "Not run.",
                )
            )
            if pr_number is None:
                created = self._github_client.create_pull_request(
                    repo=repo,
                    title=self._build_pr_title(issue_number=issue_number, issue_title=issue.title),
                    head=branch,
                    base=base_branch,
                    body=pr_body,
                )
                pr_number = created.number
                pr_url = created.html_url
                state.pr_number = pr_number
            else:
                # Avoid overwriting human edits: only enforce required closes line.
                existing = self._github_client.get_pull_request(repo=repo, pr_number=pr_number)
                required_line = f"Closes #{issue_number}"
                if (existing.body or "").find(required_line) < 0:
                    self._github_client.update_pull_request_body(
                        repo=repo,
                        pr_number=pr_number,
                        body=pr_body,
                    )

            state.last_seen_comment_id = max(state.last_seen_comment_id, max_comment_id)
            state.last_run_status = "success" if provider_result.success else "failed"
            state.last_error = None if provider_result.success else provider_result.summary
            self._state_store.save(state)
            self._write_result_file(state=state, pr_url=pr_url)

            comment_body = (
                self._build_success_comment(
                    state=state,
                    pr_url=pr_url,
                    provider_summary=provider_result.summary,
                )
                if provider_result.success
                else self._build_failure_comment(
                    state=state,
                    pr_url=pr_url,
                    provider_summary=provider_result.summary,
                    provider_log_excerpt=provider_result.log_excerpt,
                )
            )
            self._github_client.create_issue_comment(
                repo=repo,
                issue_number=issue_number,
                body=comment_body,
            )
            return RunResult(success=provider_result.success, message=provider_result.summary)
        except StopRequestedError as exc:
            state.last_run_status = "failed"
            state.last_error = str(exc)
            self._state_store.save(state)
            self._write_result_file(state=state, pr_url=None)
            return RunResult(success=False, message=str(exc))
        except (GitHubApiError, GitCommandError) as exc:
            state.last_run_status = "failed"
            state.last_error = str(exc)
            self._state_store.save(state)
            self._write_result_file(state=state, pr_url=None)
            self._safe_report_failure(repo=repo, issue_number=issue_number, error=str(exc))
            return RunResult(success=False, message=str(exc))
        except Exception as exc:  # noqa: BLE001
            state.last_run_status = "failed"
            state.last_error = f"Unhandled error: {exc}"
            self._state_store.save(state)
            self._write_result_file(state=state, pr_url=None)
            self._safe_report_failure(repo=repo, issue_number=issue_number, error=str(exc))
            return RunResult(success=False, message=str(exc))

    def _resolve_context(self, event: WorkerEvent) -> tuple[str, int, str]:
        state_exists = self._state_store.paths.state_file.exists()
        if state_exists:
            state = self._state_store.load()
            return state.repo, state.issue_number, state.base_branch
        if event.repo is None or event.issue_number is None:
            raise ValueError("repo and issue_number are required for initial start.")
        base_branch = event.base_branch or "main"
        return event.repo, event.issue_number, base_branch

    def _run_verify_commands_or_raise(self, *, repo_dir: str) -> str | None:
        if not self._verify_commands:
            return None
        # Verification is operator-controlled; run via bash for convenience.
        from app.subprocess_utils import CommandRunner  # local import to keep module sync

        runner = CommandRunner()
        lines: list[str] = []
        for cmd in self._verify_commands:
            result = runner.run(args=["bash", "-lc", cmd], cwd=repo_dir)
            lines.append(f"$ {cmd}\n{(result.stdout + result.stderr).strip()}")
            if result.exit_code != 0:
                joined = "\n\n".join(lines).strip()
                raise RuntimeError(f"Verification command failed.\n\n{joined}")
        joined = "\n\n".join(lines).strip()
        return joined or None

    @staticmethod
    def _build_commit_message(*, issue_number: int, issue_title: str) -> str:
        clean_title = " ".join(issue_title.split())
        if len(clean_title) > 60:
            clean_title = clean_title[:57] + "..."
        return f"Implement #{issue_number}: {clean_title}"

    @staticmethod
    def _build_pr_title(*, issue_number: int, issue_title: str) -> str:
        clean_title = " ".join(issue_title.split())
        if len(clean_title) > 80:
            clean_title = clean_title[:77] + "..."
        return f"#{issue_number}: {clean_title}"

    @staticmethod
    def _build_success_comment(
        *,
        state: WorkerState,
        pr_url: str | None,
        provider_summary: str,
    ) -> str:
        now = datetime.now(UTC).isoformat()
        pr_part = f"- PR: {pr_url} (#{state.pr_number})" if pr_url else f"- PR: #{state.pr_number}"
        return (
            "✅ Engineer Bot run completed.\n\n"
            f"- Time (UTC): {now}\n"
            f"- Branch: `{state.branch}`\n"
            f"- Head SHA: `{state.last_head_sha or ''}`\n"
            f"{pr_part}\n\n"
            f"Summary:\n{provider_summary}\n"
        )

    @staticmethod
    def _build_failure_comment(
        *,
        state: WorkerState,
        pr_url: str | None,
        provider_summary: str,
        provider_log_excerpt: str | None,
    ) -> str:
        now = datetime.now(UTC).isoformat()
        pr_part = f"- PR: {pr_url} (#{state.pr_number})" if pr_url else f"- PR: #{state.pr_number}"
        log_part = (
            f"\n\nLog excerpt:\n```\n{provider_log_excerpt.strip()}\n```"
            if provider_log_excerpt and provider_log_excerpt.strip()
            else ""
        )
        return (
            "❌ Engineer Bot run failed.\n\n"
            f"- Time (UTC): {now}\n"
            f"- Branch: `{state.branch}`\n"
            f"- Head SHA: `{state.last_head_sha or ''}`\n"
            f"{pr_part}\n\n"
            f"Summary:\n{provider_summary}\n"
            f"{log_part}\n"
        )

    def _safe_report_failure(self, *, repo: str, issue_number: int, error: str) -> None:
        try:
            self._github_client.create_issue_comment(
                repo=repo,
                issue_number=issue_number,
                body=f"❌ Engineer Bot run failed.\n\nError:\n{error}\n",
            )
        except Exception:  # noqa: BLE001
            # Avoid masking original failure.
            return

    def _write_result_file(self, *, state: WorkerState, pr_url: str | None) -> None:
        """Writes a machine-readable result under /work/out/result.json (best-effort)."""

        try:
            self._state_store.ensure_directories()
            out_path = self._state_store.paths.out_dir / "result.json"
            tmp_path = out_path.with_suffix(".json.tmp")
            payload = {
                "repo": state.repo,
                "issue_number": state.issue_number,
                "base_branch": state.base_branch,
                "branch": state.branch,
                "pr_number": state.pr_number,
                "pr_url": pr_url,
                "last_seen_comment_id": state.last_seen_comment_id,
                "last_head_sha": state.last_head_sha,
                "last_run_status": state.last_run_status,
                "last_error": state.last_error,
            }
            tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp_path, out_path)
        except Exception:  # noqa: BLE001
            return

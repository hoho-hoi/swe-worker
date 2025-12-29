"""Git operations for the worker.

Security requirements:
  - Do not embed tokens into persisted remote URLs.
  - Avoid printing tokens into logs.

This module authenticates to GitHub using a temporary HTTP extra header passed
via ``git -c`` options.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from app.integrations.process.subprocess_utils import CommandResult, CommandRunner


class GitCommandError(RuntimeError):
    """Raised when a git command fails."""

    def __init__(
        self,
        *,
        message: str,
        command_display: str | None = None,
        exit_code: int | None = None,
        stderr: str | None = None,
    ) -> None:
        parts: list[str] = [message]
        if command_display:
            parts.append(f"command={command_display}")
        if exit_code is not None:
            parts.append(f"exit_code={exit_code}")
        if stderr:
            stderr_text = stderr.strip()
            if len(stderr_text) > 2000:
                stderr_text = stderr_text[-2000:]
            parts.append(f"stderr={stderr_text}")
        super().__init__(" | ".join(parts))
        self.stderr = stderr


@dataclass(frozen=True)
class GitOpsConfig:
    """Configuration for Git operations."""

    author_name: str
    author_email: str


class GitOps:
    """Performs clone/checkout/commit/push for the worker."""

    def __init__(self, *, config: GitOpsConfig, runner: CommandRunner | None = None) -> None:
        self._config = config
        self._runner = runner or CommandRunner()

    def clone_if_needed(
        self,
        *,
        repo: str,
        dest_dir: str,
        base_branch: str,
        github_token: str,
    ) -> None:
        """Clones repository into dest_dir if not already cloned."""

        dest = Path(dest_dir)
        if (dest / ".git").exists():
            return

        dest.parent.mkdir(parents=True, exist_ok=True)
        repo_url = self._repo_https_url(repo)
        extraheader = self._github_extraheader_value(github_token)
        result = self._runner.run(
            args=[
                "git",
                "-c",
                f"http.https://github.com/.extraheader={extraheader}",
                "clone",
                "--branch",
                base_branch,
                "--single-branch",
                repo_url,
                str(dest),
            ],
        )
        if result.exit_code != 0:
            # Provide more helpful error message for authentication failures
            error_msg = "git clone failed"
            if "403" in result.stderr or "Permission" in result.stderr or "denied" in result.stderr:
                error_msg = (
                    "git clone failed: Authentication or permission error. "
                    "Please verify that the GitHub token has 'repo' scope and read access "
                    "to the repository."
                )
            raise GitCommandError(
                message=error_msg,
                command_display=self._format_command_for_display(
                    [
                        "git",
                        "-c",
                        "http.https://github.com/.extraheader=<REDACTED>",
                        "clone",
                        "--branch",
                        base_branch,
                        "--single-branch",
                        repo_url,
                        str(dest),
                    ]
                ),
                exit_code=result.exit_code,
                stderr=result.stderr,
            )

    def ensure_branch_checked_out(
        self,
        *,
        repo_dir: str,
        base_branch: str,
        branch: str,
        github_token: str,
    ) -> None:
        """Ensures the working directory is on the requested branch."""

        if branch == base_branch:
            raise ValueError("branch must not be the same as base_branch.")

        extraheader = self._github_extraheader_value(github_token)
        self._run_git(
            repo_dir,
            args=["fetch", "origin", base_branch],
            extraheader=extraheader,
        )
        self._run_git(repo_dir, args=["checkout", base_branch], extraheader=extraheader)
        self._run_git(
            repo_dir,
            args=["reset", "--hard", f"origin/{base_branch}"],
            extraheader=extraheader,
        )

        branch_exists = self._run_git(
            repo_dir,
            args=["rev-parse", "--verify", branch],
            extraheader=extraheader,
            allow_failure=True,
        )
        if branch_exists.exit_code == 0:
            self._run_git(repo_dir, args=["checkout", branch], extraheader=extraheader)
        else:
            self._run_git(
                repo_dir,
                args=["checkout", "-b", branch],
                extraheader=extraheader,
            )

    def get_head_sha(self, *, repo_dir: str) -> str:
        """Returns HEAD SHA."""

        result = self._run_git(repo_dir, args=["rev-parse", "HEAD"])
        sha = result.stdout.strip()
        if not sha:
            raise GitCommandError(message="Failed to read HEAD sha.")
        return sha

    def get_status_porcelain(self, *, repo_dir: str) -> str:
        """Returns `git status --porcelain` output."""

        result = self._run_git(repo_dir, args=["status", "--porcelain"])
        return result.stdout

    def commit_all_if_dirty(self, *, repo_dir: str, message: str) -> str | None:
        """Commits all changes if there are any.

        Returns:
            Commit SHA if committed, otherwise None.
        """

        status = self.get_status_porcelain(repo_dir=repo_dir).strip()
        if not status:
            return None

        self._run_git(repo_dir, args=["config", "user.name", self._config.author_name])
        self._run_git(repo_dir, args=["config", "user.email", self._config.author_email])
        self._run_git(repo_dir, args=["add", "-A"])
        commit = self._run_git(repo_dir, args=["commit", "-m", message])
        if commit.exit_code != 0:
            raise GitCommandError(message="git commit failed", stderr=commit.stderr)
        return self.get_head_sha(repo_dir=repo_dir)

    def push_branch(self, *, repo_dir: str, branch: str, github_token: str) -> None:
        """Pushes branch to origin without persisting token in git config."""

        extraheader = self._github_extraheader_value(github_token)
        result = self._run_git(
            repo_dir,
            args=["push", "-u", "origin", branch],
            extraheader=extraheader,
        )
        if result.exit_code != 0:
            # Provide more helpful error message for authentication failures
            error_msg = "git push failed"
            if "403" in result.stderr or "Permission" in result.stderr or "denied" in result.stderr:
                error_msg = (
                    "git push failed: Authentication or permission error. "
                    "Please verify that the GitHub token has 'repo' scope and write access "
                    "to the repository."
                )
            raise GitCommandError(
                message=error_msg,
                command_display=self._format_command_for_display(
                    ["git", "-C", repo_dir, "push", "-u", "origin", branch]
                ),
                exit_code=result.exit_code,
                stderr=result.stderr,
            )

    def verify_remote_access(self, *, repo: str, github_token: str) -> None:
        """Verifies the token can authenticate to the repository via Git HTTPS.

        This is a lightweight check used for validation before running a full worker loop.

        Args:
            repo: Repository in "owner/repo" format.
            github_token: GitHub token used for authentication.

        Raises:
            GitCommandError: If remote access fails.
        """
        repo_url = self._repo_https_url(repo)
        extraheader = self._github_extraheader_value(github_token)
        # `ls-remote` is a cheap way to validate Git HTTPS auth without cloning.
        result = self._runner.run(
            args=[
                "git",
                "-c",
                f"http.https://github.com/.extraheader={extraheader}",
                "ls-remote",
                repo_url,
                "HEAD",
            ]
        )
        if result.exit_code != 0:
            raise GitCommandError(
                message="git ls-remote failed",
                command_display=self._format_command_for_display(
                    [
                        "git",
                        "-c",
                        "http.https://github.com/.extraheader=<REDACTED>",
                        "ls-remote",
                        repo_url,
                        "HEAD",
                    ]
                ),
                exit_code=result.exit_code,
                stderr=result.stderr,
            )

    def _run_git(
        self,
        repo_dir: str,
        *,
        args: list[str],
        extraheader: str | None = None,
        allow_failure: bool = False,
    ) -> CommandResult:
        cmd = ["git", "-C", repo_dir]
        if extraheader is not None:
            cmd.extend(["-c", f"http.https://github.com/.extraheader={extraheader}"])
        cmd.extend(args)
        result = self._runner.run(args=cmd)
        if (not allow_failure) and result.exit_code != 0:
            raise GitCommandError(
                message="git command failed",
                command_display=self._format_command_for_display(
                    self._redact_command_args_for_display(cmd)
                ),
                exit_code=result.exit_code,
                stderr=result.stderr,
            )
        return result

    @staticmethod
    def _repo_https_url(repo: str) -> str:
        return f"https://github.com/{repo}.git"

    @staticmethod
    def _github_extraheader_value(token: str) -> str:
        """Builds `http.*.extraHeader` value for GitHub HTTPS auth.

        GitHub recommends basic auth with username `x-access-token` and the token as password.
        The `http.extraHeader` config expects a full HTTP header line.
        """
        raw = f"x-access-token:{token}".encode()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"Authorization: Basic {b64}"

    @staticmethod
    def _redact_command_args_for_display(args: list[str]) -> list[str]:
        redacted: list[str] = []
        for arg in args:
            if "http.https://github.com/.extraheader=" in arg:
                redacted.append("http.https://github.com/.extraheader=<REDACTED>")
            else:
                redacted.append(arg)
        return redacted

    @staticmethod
    def _format_command_for_display(args: list[str]) -> str:
        # Keep it readable and safe for logs/comments.
        return " ".join(args)

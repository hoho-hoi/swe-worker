"""OpenHands provider (CLI-based).

This provider calls an OpenHands-compatible command as a subprocess, with the
repository directory set to ``repo_path``. The invoked command is expected to
apply changes directly to the working tree.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from app.providers.base import Provider, ProviderResult, Task
from app.subprocess_utils import CommandRunner


@dataclass(frozen=True)
class OpenHandsProviderConfig:
    """Configuration for OpenHandsProvider."""

    command_line: str
    additional_env: dict[str, str]


class OpenHandsProvider(Provider):
    """Provider that invokes OpenHands via CLI."""

    def __init__(
        self,
        *,
        config: OpenHandsProviderConfig,
        runner: CommandRunner | None = None,
    ) -> None:
        self._config = config
        self._command_args = self._parse_command_args(self._config.command_line)
        self._runner = runner or CommandRunner()

    def run(self, *, task: Task, repo_path: str) -> ProviderResult:
        """Runs OpenHands in ``repo_path``.

        The provider writes a human-readable task file under
        ``.swe-worker/task.md`` and passes its path via ``SWE_WORKER_TASK_FILE``.
        This allows flexible wrapper scripts on the OpenHands side.
        """

        repo_dir = Path(repo_path)
        task_dir = repo_dir / ".swe-worker"
        task_dir.mkdir(parents=True, exist_ok=True)
        task_file = task_dir / "task.md"
        task_file.write_text(self._render_task_markdown(task), encoding="utf-8")

        env = dict(self._config.additional_env)
        env["SWE_WORKER_TASK_FILE"] = str(task_file)

        try:
            result = self._runner.run(
                args=list(self._command_args),
                cwd=repo_dir,
                env=env,
            )
        except FileNotFoundError:
            return ProviderResult(
                success=False,
                summary=(
                    "OpenHands command was not found. "
                    "Ensure OPENHANDS_COMMAND points to an executable available in PATH "
                    "(e.g., 'openhands', 'uv run openhands', or an absolute path)."
                ),
                log_excerpt=None,
            )
        success = result.exit_code == 0
        log_excerpt = (result.stdout + "\n" + result.stderr).strip()
        if len(log_excerpt) > 4000:
            log_excerpt = log_excerpt[-4000:]

        if not success:
            return ProviderResult(
                success=False,
                summary="OpenHands command failed.",
                log_excerpt=log_excerpt or None,
            )
        return ProviderResult(
            success=True,
            summary="OpenHands command completed successfully.",
            log_excerpt=log_excerpt or None,
        )

    @staticmethod
    def _render_task_markdown(task: Task) -> str:
        return (
            f"# Task\n\n"
            f"Repository: {task.repo}\n\n"
            f"Issue: #{task.issue_number} - {task.issue_title}\n\n"
            f"## Issue body\n\n{task.issue_body}\n\n"
            f"## New comments\n\n{task.comments_markdown}\n\n"
            f"## Constraints\n\n{task.constraints_markdown}\n"
        )

    @staticmethod
    def _parse_command_args(command_line: str) -> tuple[str, ...]:
        args = tuple(shlex.split(command_line))
        if not args:
            raise ValueError("OPENHANDS_COMMAND must not be empty.")
        return args

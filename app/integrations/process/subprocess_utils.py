"""Utilities for running subprocesses safely.

This module avoids echoing sensitive values (e.g., GitHub tokens) to logs by
allowing redaction and by not returning full command lines by default.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    """Result of running a command."""

    exit_code: int
    stdout: str
    stderr: str


class CommandRunner:
    """Runs OS commands with controlled environment and output capturing."""

    def run(
        self,
        *,
        args: list[str],
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        """Runs a command and captures stdout/stderr.

        Args:
            args: Command arguments (no shell).
            cwd: Working directory.
            env: Environment variables to merge with current environment.
            timeout_seconds: Optional timeout.

        Returns:
            Captured result.

        Raises:
            TimeoutError: If timeout is exceeded.
            OSError: If process cannot be started.
        """

        merged_env = os.environ.copy()
        if env is not None:
            merged_env.update(env)

        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd is not None else None,
            env=merged_env,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        return CommandResult(
            exit_code=int(completed.returncode),
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

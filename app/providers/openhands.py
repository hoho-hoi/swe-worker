"""OpenHands provider (CLI-based).

This provider calls an OpenHands-compatible command as a subprocess, with the
repository directory set to ``repo_path``. The invoked command is expected to
apply changes directly to the working tree.
"""

from __future__ import annotations

import logging
import os
import shlex
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from app.core.config import AppSettings
from app.integrations.process.subprocess_utils import CommandRunner
from app.providers.base import Provider, ProviderResult, Task


@dataclass(frozen=True)
class OpenHandsProviderConfig:
    """Configuration for OpenHandsProvider."""

    command_line: str
    additional_env: dict[str, str]


class OpenHandsProvider(Provider):
    """Provider that invokes OpenHands via CLI."""

    _logger = logging.getLogger(__name__)

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

        settings_error = self._ensure_agent_settings_from_env(env)
        if settings_error is not None:
            return ProviderResult(success=False, summary=settings_error, log_excerpt=None)

        command_args = self._build_effective_command_args(task_file=task_file)
        self._logger.info(
            "OpenHands started: command=%s repo_dir=%s",
            " ".join(command_args),
            str(repo_dir),
        )
        start_time = time.monotonic()
        stop_event = threading.Event()

        def log_heartbeat() -> None:
            # Periodic heartbeat so operators can tell OpenHands is still running.
            while not stop_event.wait(30.0):
                elapsed = int(time.monotonic() - start_time)
                self._logger.info("OpenHands still running: elapsed_seconds=%s", elapsed)

        timeout_seconds: int | None = None
        # Read default timeout from environment via AppSettings (same .env loader/normalizer).
        try:
            timeout_seconds = int(AppSettings().openhands_timeout_seconds)
        except Exception:  # noqa: BLE001
            timeout_seconds = None

        try:
            heartbeat_thread = threading.Thread(target=log_heartbeat, daemon=True)
            heartbeat_thread.start()
            result = self._runner.run(
                args=command_args,
                cwd=repo_dir,
                env=env,
                timeout_seconds=timeout_seconds,
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
        except TimeoutError:
            return ProviderResult(
                success=False,
                summary=(
                    "OpenHands command timed out. Increase OPENHANDS_TIMEOUT_SECONDS if needed."
                ),
                log_excerpt=None,
            )
        finally:
            stop_event.set()
            elapsed = int(time.monotonic() - start_time)
            self._logger.info("OpenHands finished: elapsed_seconds=%s", elapsed)
        success = result.exit_code == 0
        log_excerpt = (result.stdout + "\n" + result.stderr).strip()
        if len(log_excerpt) > 4000:
            log_excerpt = log_excerpt[-4000:]

        if not success:
            if log_excerpt:
                # Surface enough context to debug without having to open issue comments.
                self._logger.error(
                    "OpenHands command failed: exit_code=%s excerpt_tail=%s",
                    result.exit_code,
                    log_excerpt[-800:],
                )
            return ProviderResult(
                success=False,
                summary=f"OpenHands command failed (exit_code={result.exit_code}).",
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

    def _build_effective_command_args(self, *, task_file: Path) -> list[str]:
        """Builds an OpenHands command line suitable for non-interactive execution.

        OpenHands CLI defaults to an interactive TUI with manual approvals. In a worker
        environment we want headless, non-interactive execution seeded by the task file.

        Args:
            task_file: Path to the rendered task file.

        Returns:
            Command arguments for subprocess execution.
        """
        args = list(self._command_args)

        has_file = ("--file" in args) or ("-f" in args)
        has_task = ("--task" in args) or ("-t" in args)
        has_headless = "--headless" in args
        has_always_approve = "--always-approve" in args
        has_llm_approve = "--llm-approve" in args
        has_exit_without_confirmation = "--exit-without-confirmation" in args

        # Ensure the initial task is provided.
        if not has_file and not has_task:
            args.extend(["--file", str(task_file)])

        # Ensure non-interactive execution.
        if not has_headless:
            args.append("--headless")
        if not (has_always_approve or has_llm_approve):
            args.append("--always-approve")
        if not has_exit_without_confirmation:
            args.append("--exit-without-confirmation")

        return args

    @staticmethod
    def _ensure_agent_settings_from_env(env: dict[str, str]) -> str | None:
        """Ensures OpenHands CLI has an agent_settings.json when model info is provided.

        OpenHands CLI (1.6.0) primarily loads LLM configuration from
        ``~/.openhands/agent_settings.json``. This worker supports specifying model
        via environment variables and writes the settings file to the HOME provided
        in env (which the worker sets to a persistent directory).

        Returns:
            Error message string if configuration is invalid, otherwise None.
        """

        requested_model = (env.get("LLM_MODEL") or "").strip()
        openai_model = (env.get("OPENAI_MODEL") or "").strip()
        if not requested_model and openai_model:
            requested_model = f"openai/{openai_model}"

        if not requested_model:
            # No override requested; keep existing OpenHands settings if any.
            return None

        openai_key = (env.get("OPENAI_API_KEY") or "").strip()
        gemini_key = (env.get("GOOGLE_API_KEY") or env.get("GEMINI_API_KEY") or "").strip()
        has_openai = bool(openai_key)
        has_gemini = bool(gemini_key)

        if "/" not in requested_model:
            if has_openai and has_gemini:
                return (
                    "LLM_MODEL is ambiguous when both OpenAI and Gemini keys are provided. "
                    "Use an explicit provider prefix, e.g., 'openai/<model>' or 'gemini/<model>'."
                )
            if has_openai:
                requested_model = f"openai/{requested_model}"
            elif has_gemini:
                requested_model = f"gemini/{requested_model}"
            else:
                return (
                    "LLM_MODEL must include a provider prefix (e.g., 'openai/<model>' or "
                    "'gemini/<model>') unless exactly one provider key is configured."
                )

        api_key = (env.get("LLM_API_KEY") or "").strip()
        if not api_key:
            if requested_model.startswith("openai/"):
                api_key = openai_key
            elif requested_model.startswith("gemini/"):
                api_key = gemini_key

        if not api_key:
            return (
                "LLM model was specified but no matching API key was provided. "
                "Set LLM_API_KEY, or set OPENAI_API_KEY for openai/* models, "
                "or set GOOGLE_API_KEY/GEMINI_API_KEY for gemini/* models."
            )

        base_url = (env.get("LLM_BASE_URL") or env.get("OPENAI_BASE_URL") or "").strip() or None
        home_dir = Path(env.get("HOME") or os.path.expanduser("~"))
        settings_dir = home_dir / ".openhands"
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path = settings_dir / "agent_settings.json"

        # Build an OpenHands Agent spec via SDK so schema stays compatible.
        try:
            from openhands.sdk import LLM  # type: ignore[import-not-found]
            from openhands.tools.preset import get_default_agent  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            return f"Failed to import OpenHands SDK to generate settings: {exc}"

        llm = LLM(
            model=requested_model,
            api_key=SecretStr(api_key),
            base_url=base_url,
            usage_id="agent",
        )
        agent = get_default_agent(llm=llm, cli_mode=True)
        settings_path.write_text(
            agent.model_dump_json(context={"expose_secrets": True}) + "\n",
            encoding="utf-8",
        )
        return None

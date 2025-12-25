from __future__ import annotations

from pathlib import Path

from app.providers.base import Task
from app.providers.openhands import OpenHandsProvider, OpenHandsProviderConfig


class _FileNotFoundRunner:
    def run(
        self, *, args: list[str], cwd: str | Path | None = None, env=None, timeout_seconds=None
    ):
        raise FileNotFoundError


class _SuccessRunner:
    def __init__(self) -> None:
        self.last_args: list[str] | None = None

    def run(
        self, *, args: list[str], cwd: str | Path | None = None, env=None, timeout_seconds=None
    ):
        self.last_args = list(args)

        class _Result:
            exit_code = 0
            stdout = "ok"
            stderr = ""

        return _Result()


def test_openhands_provider_returns_failure_when_command_not_found(tmp_path: Path) -> None:
    provider = OpenHandsProvider(
        config=OpenHandsProviderConfig(command_line="openhands", additional_env={}),
        runner=_FileNotFoundRunner(),
    )
    result = provider.run(
        task=Task(
            repo="owner/repo",
            issue_number=1,
            issue_title="t",
            issue_body="b",
            comments_markdown="",
            constraints_markdown="",
        ),
        repo_path=str(tmp_path),
    )
    assert result.success is False
    assert "not found" in result.summary.lower()


def test_openhands_provider_parses_command_line(tmp_path: Path) -> None:
    runner = _SuccessRunner()
    provider = OpenHandsProvider(
        config=OpenHandsProviderConfig(command_line="uv run openhands", additional_env={}),
        runner=runner,
    )
    result = provider.run(
        task=Task(
            repo="owner/repo",
            issue_number=1,
            issue_title="t",
            issue_body="b",
            comments_markdown="",
            constraints_markdown="",
        ),
        repo_path=str(tmp_path),
    )
    assert result.success is True
    assert runner.last_args == ["uv", "run", "openhands"]

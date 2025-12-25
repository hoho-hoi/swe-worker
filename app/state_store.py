"""State persistence for an issue worker.

State is stored as JSON under ``/work/state/state.json`` (container-local).
The implementation uses atomic file replacement to avoid partial writes.
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field

from app.work_paths import WorkPaths


class WorkerState(BaseModel):
    """Persistent state for a single issue worker."""

    repo: str = Field(..., description="Repository in 'owner/repo' format.")
    issue_number: int = Field(..., ge=1)
    base_branch: str = Field("main")
    branch: str = Field(..., description="Working branch name.")

    pr_number: int | None = Field(default=None, ge=1)
    last_seen_comment_id: int = Field(default=0, ge=0)
    last_head_sha: str | None = Field(default=None)

    last_run_status: str = Field(default="idle")  # success|failed|running|idle
    last_error: str | None = Field(default=None)


class StateStore:
    """Read/write access to ``WorkerState``."""

    def __init__(self, *, paths: WorkPaths) -> None:
        self._paths = paths

    @property
    def paths(self) -> WorkPaths:
        """Returns resolved paths under WORK_ROOT."""

        return self._paths

    def ensure_directories(self) -> None:
        """Ensures required persistence directories exist."""

        self._paths.repo_dir.mkdir(parents=True, exist_ok=True)
        self._paths.state_dir.mkdir(parents=True, exist_ok=True)
        self._paths.logs_dir.mkdir(parents=True, exist_ok=True)
        self._paths.out_dir.mkdir(parents=True, exist_ok=True)

    def load_or_initialize(
        self,
        *,
        repo: str,
        issue_number: int,
        base_branch: str,
        branch: str,
    ) -> WorkerState:
        """Loads state from disk if present, otherwise initializes a new state."""

        self.ensure_directories()
        if self._paths.state_file.exists():
            return self.load()
        initial_state = WorkerState(
            repo=repo,
            issue_number=issue_number,
            base_branch=base_branch,
            branch=branch,
            pr_number=None,
            last_seen_comment_id=0,
            last_head_sha=None,
            last_run_status="idle",
            last_error=None,
        )
        self.save(initial_state)
        return initial_state

    def load(self) -> WorkerState:
        """Loads worker state from disk."""

        self.ensure_directories()
        raw = self._paths.state_file.read_text(encoding="utf-8")
        payload = json.loads(raw)
        return WorkerState.model_validate(payload)

    def save(self, state: WorkerState) -> None:
        """Writes worker state to disk atomically."""

        self.ensure_directories()
        tmp_path = self._paths.state_file.with_suffix(".json.tmp")
        serialized = json.dumps(state.model_dump(), indent=2, ensure_ascii=False)
        tmp_path.write_text(serialized + "\n", encoding="utf-8")
        os.replace(tmp_path, self._paths.state_file)

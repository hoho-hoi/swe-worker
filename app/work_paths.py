"""Container-local working paths.

This project intentionally avoids host volume dependencies. All state and
working files live under a single root inside the container.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkPaths:
    """Resolved paths used by the worker."""

    work_root: Path
    repo_dir: Path
    state_dir: Path
    state_file: Path
    logs_dir: Path
    out_dir: Path


def get_default_work_paths() -> WorkPaths:
    """Returns the default container-local work paths."""

    work_root = Path("/work")
    repo_dir = work_root / "repo"
    state_dir = work_root / "state"
    logs_dir = work_root / "logs"
    out_dir = work_root / "out"
    return WorkPaths(
        work_root=work_root,
        repo_dir=repo_dir,
        state_dir=state_dir,
        state_file=state_dir / "state.json",
        logs_dir=logs_dir,
        out_dir=out_dir,
    )


def get_work_paths(*, work_root: str | Path) -> WorkPaths:
    """Returns work paths for a given root (used for tests)."""

    root = Path(work_root)
    repo_dir = root / "repo"
    state_dir = root / "state"
    logs_dir = root / "logs"
    out_dir = root / "out"
    return WorkPaths(
        work_root=root,
        repo_dir=repo_dir,
        state_dir=state_dir,
        state_file=state_dir / "state.json",
        logs_dir=logs_dir,
        out_dir=out_dir,
    )

from __future__ import annotations

from pathlib import Path

from app.state_store import StateStore, WorkerState


def test_state_store_load_or_initialize_creates_state_file(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path))
    state = store.load_or_initialize(
        repo="owner/repo",
        issue_number=123,
        base_branch="main",
        branch="agent/issue-123",
    )
    assert store.paths.state_file.exists()
    assert state.repo == "owner/repo"
    assert state.issue_number == 123
    assert state.base_branch == "main"
    assert state.branch == "agent/issue-123"


def test_state_store_save_is_atomic_and_load_roundtrips(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path))
    state = WorkerState(
        repo="owner/repo",
        issue_number=1,
        base_branch="main",
        branch="agent/issue-1",
        pr_number=10,
        last_seen_comment_id=5,
        last_head_sha="abc",
        last_run_status="success",
        last_error=None,
    )
    store.save(state)

    tmp_file = store.paths.state_file.with_suffix(".json.tmp")
    assert not tmp_file.exists()

    loaded = store.load()
    assert loaded.model_dump() == state.model_dump()

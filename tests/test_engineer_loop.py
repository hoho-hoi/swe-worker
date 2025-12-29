from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.engineer_loop import EngineerLoop, WorkerEvent
from app.github_client import Issue, IssueComment, PullRequest, PullRequestCreated
from app.pr_template import PullRequestBodyRenderer
from app.providers.base import Provider, ProviderResult, Task
from app.state_store import StateStore
from app.work_paths import get_work_paths


class _FakeProvider(Provider):
    def __init__(self, *, result: ProviderResult) -> None:
        self._result = result
        self.last_task: Task | None = None

    def run(self, *, task: Task, repo_path: str) -> ProviderResult:
        self.last_task = task
        return self._result


@dataclass
class _FakeGitOps:
    commit_sha: str | None
    head_sha: str
    pushed_branches: list[str]

    def clone_if_needed(
        self, *, repo: str, dest_dir: str, base_branch: str, github_token: str
    ) -> None:
        Path(dest_dir).mkdir(parents=True, exist_ok=True)

    def ensure_branch_checked_out(
        self, *, repo_dir: str, base_branch: str, branch: str, github_token: str
    ) -> None:
        return

    def commit_all_if_dirty(self, *, repo_dir: str, message: str) -> str | None:
        return self.commit_sha

    def push_branch(self, *, repo_dir: str, branch: str, github_token: str) -> None:
        self.pushed_branches.append(branch)

    def get_head_sha(self, *, repo_dir: str) -> str:
        return self.head_sha


@dataclass
class _FakeGitHubClient:
    issue: Issue
    comments: list[IssueComment]
    created_prs: list[tuple[str, str, str, str]]
    issue_comments: list[str]
    updated_pr_bodies: list[str]
    existing_pr_body: str

    def get_issue(self, *, repo: str, issue_number: int) -> Issue:
        return self.issue

    def list_issue_comments_since(
        self, *, repo: str, issue_number: int, last_seen_comment_id: int
    ) -> list[IssueComment]:
        return [c for c in self.comments if c.id > last_seen_comment_id]

    def create_pull_request(self, *, repo: str, title: str, head: str, base: str, body: str):
        self.created_prs.append((title, head, base, body))
        return PullRequestCreated(number=99, html_url="https://example/pr/99", body=body)

    def update_pull_request_body(self, *, repo: str, pr_number: int, body: str) -> None:
        self.updated_pr_bodies.append(body)

    def get_pull_request(self, *, repo: str, pr_number: int) -> PullRequest:
        return PullRequest(
            number=pr_number, html_url="https://example/pr/99", body=self.existing_pr_body
        )

    def create_issue_comment(self, *, repo: str, issue_number: int, body: str) -> int:
        self.issue_comments.append(body)
        return 1


def test_engineer_loop_creates_pr_and_updates_state(tmp_path: Path) -> None:
    store = StateStore(paths=get_work_paths(work_root=tmp_path))
    gh = _FakeGitHubClient(
        issue=Issue(number=123, title="Test issue", body="Body"),
        comments=[IssueComment(id=10, body="c1"), IssueComment(id=11, body="c2")],
        created_prs=[],
        issue_comments=[],
        updated_pr_bodies=[],
        existing_pr_body="",
    )
    git_ops = _FakeGitOps(commit_sha="commit1", head_sha="head1", pushed_branches=[])
    provider = _FakeProvider(result=ProviderResult(success=True, summary="done", log_excerpt=None))

    template_dir = tmp_path / "templates"
    template_dir.mkdir(parents=True)
    (template_dir / "pr_body.md").write_text(
        (
            "Summary\n{{ summary }}\n\n"
            "How to test\n{{ how_to_test }}\n\n"
            "Tracking\nCloses #{{ issue_number }}\n"
        ),
        encoding="utf-8",
    )
    renderer = PullRequestBodyRenderer(template_dir=str(template_dir))

    loop = EngineerLoop(
        state_store=store,
        github_client=gh,  # type: ignore[arg-type]
        git_ops=git_ops,  # type: ignore[arg-type]
        provider=provider,
        pr_body_renderer=renderer,
        github_token="token",
        verify_commands=[],
    )

    result = loop.run(
        event=WorkerEvent(type="start", repo="owner/repo", issue_number=123, base_branch="main")
    )
    assert result.success is True
    assert gh.created_prs
    assert git_ops.pushed_branches == ["agent/issue-123"]

    state = store.load()
    assert state.pr_number == 99
    assert state.last_seen_comment_id == 11
    assert state.last_head_sha == "head1"
    assert state.last_run_status == "success"
    assert gh.issue_comments and gh.issue_comments[-1].startswith("✅")


def test_engineer_loop_rerun_reuses_existing_pr(tmp_path: Path) -> None:
    store = StateStore(paths=get_work_paths(work_root=tmp_path))
    _ = store.load_or_initialize(
        repo="owner/repo",
        issue_number=123,
        base_branch="main",
        branch="agent/issue-123",
    )
    state = store.load()
    state.pr_number = 99
    store.save(state)

    gh = _FakeGitHubClient(
        issue=Issue(number=123, title="Test issue", body="Body"),
        comments=[],
        created_prs=[],
        issue_comments=[],
        updated_pr_bodies=[],
        existing_pr_body="Closes #123\n",
    )
    git_ops = _FakeGitOps(commit_sha=None, head_sha="head2", pushed_branches=[])
    provider = _FakeProvider(result=ProviderResult(success=True, summary="reran", log_excerpt=None))

    template_dir = tmp_path / "templates"
    template_dir.mkdir(parents=True)
    (template_dir / "pr_body.md").write_text(
        (
            "Summary\n{{ summary }}\n\n"
            "How to test\n{{ how_to_test }}\n\n"
            "Tracking\nCloses #{{ issue_number }}\n"
        ),
        encoding="utf-8",
    )
    renderer = PullRequestBodyRenderer(template_dir=str(template_dir))

    loop = EngineerLoop(
        state_store=store,
        github_client=gh,  # type: ignore[arg-type]
        git_ops=git_ops,  # type: ignore[arg-type]
        provider=provider,
        pr_body_renderer=renderer,
        github_token="token",
        verify_commands=[],
    )

    result = loop.run(event=WorkerEvent(type="rerun"))
    assert result.success is True
    assert gh.created_prs == []
    assert gh.updated_pr_bodies == []


def test_engineer_loop_creates_pr_even_when_no_changes(tmp_path: Path) -> None:
    store = StateStore(paths=get_work_paths(work_root=tmp_path))
    gh = _FakeGitHubClient(
        issue=Issue(number=123, title="Test issue", body="Body"),
        comments=[],
        created_prs=[],
        issue_comments=[],
        updated_pr_bodies=[],
        existing_pr_body="",
    )
    git_ops = _FakeGitOps(commit_sha=None, head_sha="head1", pushed_branches=[])
    provider = _FakeProvider(result=ProviderResult(success=True, summary="done", log_excerpt=None))

    template_dir = tmp_path / "templates"
    template_dir.mkdir(parents=True)
    (template_dir / "pr_body.md").write_text(
        (
            "Summary\n{{ summary }}\n\n"
            "How to test\n{{ how_to_test }}\n\n"
            "Tracking\nCloses #{{ issue_number }}\n"
        ),
        encoding="utf-8",
    )
    renderer = PullRequestBodyRenderer(template_dir=str(template_dir))

    loop = EngineerLoop(
        state_store=store,
        github_client=gh,  # type: ignore[arg-type]
        git_ops=git_ops,  # type: ignore[arg-type]
        provider=provider,
        pr_body_renderer=renderer,
        github_token="token",
        verify_commands=[],
    )

    result = loop.run(
        event=WorkerEvent(type="start", repo="owner/repo", issue_number=123, base_branch="main")
    )
    assert result.success is True
    assert gh.created_prs
    # First PR run must push the branch to ensure the head exists.
    assert git_ops.pushed_branches == ["agent/issue-123"]

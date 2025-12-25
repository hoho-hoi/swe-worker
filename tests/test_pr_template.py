from __future__ import annotations

from pathlib import Path

from app.pr_template import PullRequestBodyInput, PullRequestBodyRenderer


def test_pr_body_renderer_appends_closes_line_when_missing(tmp_path: Path) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir(parents=True)
    (template_dir / "pr_body.md").write_text(
        "Summary\n{{ summary }}\n\nHow to test\n{{ how_to_test }}\n",
        encoding="utf-8",
    )

    renderer = PullRequestBodyRenderer(template_dir=str(template_dir))
    body = renderer.render(
        data=PullRequestBodyInput(issue_number=42, summary="did x", how_to_test="ran y")
    )
    assert "Closes #42" in body

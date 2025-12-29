"""PR body rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


@dataclass(frozen=True)
class PullRequestBodyInput:
    """Input to render a PR body."""

    issue_number: int
    summary: str
    how_to_test: str


class PullRequestBodyRenderer:
    """Renders PR body from a Jinja2 template."""

    def __init__(self, *, template_dir: str, template_name: str = "pr_body.md") -> None:
        env = Environment(
            loader=FileSystemLoader(template_dir),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        self._template = env.get_template(template_name)

    def render(self, *, data: PullRequestBodyInput) -> str:
        """Renders PR body and enforces required 'Closes #<n>' line."""

        body = self._template.render(
            issue_number=data.issue_number,
            summary=data.summary.strip(),
            how_to_test=data.how_to_test.strip(),
        ).strip()
        closes_line = f"Closes #{data.issue_number}"
        if closes_line not in body:
            body = body + "\n\n" + closes_line
        return body + "\n"


def get_default_template_dir() -> str:
    """Returns the default template directory path."""

    return str(Path(__file__).parent / "templates")



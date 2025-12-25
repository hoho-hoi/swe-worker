"""HTTP server for an issue worker.

Endpoints:
  - GET /health
  - POST /event
  - POST /stop
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import AppSettings
from app.engineer_loop import EngineerLoop, WorkerEvent
from app.git_ops import GitOps, GitOpsConfig
from app.github_client import GitHubClient, GitHubClientConfig
from app.pr_template import PullRequestBodyRenderer, get_default_template_dir
from app.providers.noop import NoOpProvider
from app.providers.openhands import OpenHandsProvider, OpenHandsProviderConfig
from app.state_store import StateStore
from app.work_paths import get_default_work_paths, get_work_paths


class EventPayload(BaseModel):
    """Payload for /event."""

    repo: str | None = Field(default=None, description="owner/repo")
    issue_number: int | None = Field(default=None, ge=1)
    base_branch: str | None = None


class EventRequest(BaseModel):
    """Event request for /event."""

    type: Literal["start", "rerun", "comment_added", "review_changes", "ci_failed"]
    payload: EventPayload = Field(default_factory=EventPayload)


@dataclass(frozen=True)
class EnqueueResult:
    """Enqueue result."""

    queued: bool
    queue_size: int


class WorkerRuntime:
    """Background runtime that processes events sequentially."""

    def __init__(self, *, settings: AppSettings, work_root: str | None = None) -> None:
        self._settings = settings
        self._stop_thread_event = threading.Event()
        self._queue: asyncio.Queue[WorkerEvent] = asyncio.Queue()
        self._consumer_task: asyncio.Task[None] | None = None

        paths = (
            get_work_paths(work_root=work_root)
            if work_root is not None
            else get_default_work_paths()
        )
        self._state_store = StateStore(paths=paths)
        self._state_store.ensure_directories()
        self._openhands_home_dir = self._state_store.paths.state_dir / "openhands_home"
        self._openhands_home_dir.mkdir(parents=True, exist_ok=True)

        self._github_client = self._build_github_client()
        self._git_ops = GitOps(
            config=GitOpsConfig(
                author_name=self._settings.git_author_name,
                author_email=self._settings.git_author_email,
            )
        )
        self._provider = self._build_provider()
        self._pr_body_renderer = PullRequestBodyRenderer(template_dir=get_default_template_dir())

    async def start(self) -> None:
        """Starts background consumer."""

        if self._consumer_task is None:
            self._consumer_task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        """Requests stop and cancels consumer."""

        self._stop_thread_event.set()
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

    async def enqueue(self, *, event: WorkerEvent) -> EnqueueResult:
        """Enqueues an event for sequential processing."""

        await self._queue.put(event)
        return EnqueueResult(queued=True, queue_size=self._queue.qsize())

    def stop_checker(self) -> bool:
        """Returns True if stop was requested."""

        return self._stop_thread_event.is_set()

    async def _consume(self) -> None:
        while not self._stop_thread_event.is_set():
            event = await self._queue.get()
            try:
                try:
                    await asyncio.to_thread(self._run_blocking, event)
                except Exception as exc:  # noqa: BLE001
                    logging.exception("Worker event failed: type=%s error=%s", event.type, exc)
            finally:
                self._queue.task_done()

    def _run_blocking(self, event: WorkerEvent) -> None:
        token = self._get_github_token()
        if token is None:
            raise RuntimeError("GITHUB_TOKEN (or ENGINEER_PAT_KEY) is required.")

        verify_commands = self._parse_verify_commands(self._settings.verify_commands)
        loop = EngineerLoop(
            state_store=self._state_store,
            github_client=self._github_client,
            git_ops=self._git_ops,
            provider=self._provider,
            pr_body_renderer=self._pr_body_renderer,
            github_token=token,
            verify_commands=verify_commands,
        )
        loop.run(event=event, stop_checker=self.stop_checker)

    def _build_github_client(self) -> GitHubClient:
        token = self._get_github_token()
        if token is None:
            # Lazy failure on /event; allow /health.
            token = "missing"
        return GitHubClient(
            config=GitHubClientConfig(
                api_base_url=self._settings.github_api_base_url,
                token=token,
            )
        )

    def _build_provider(self):
        if self._settings.openhands_command is None:
            return NoOpProvider(message="OpenHands is not configured (set OPENHANDS_COMMAND).")
        return OpenHandsProvider(
            config=OpenHandsProviderConfig(
                command_line=self._settings.openhands_command,
                additional_env=self._build_openhands_env(),
            )
        )

    @staticmethod
    def _parse_verify_commands(raw: str | None) -> list[str]:
        if raw is None:
            return []
        lines = [line.strip() for line in raw.splitlines()]
        return [line for line in lines if line and not line.startswith("#")]

    def _get_github_token(self) -> str | None:
        return self._settings.github_token or self._settings.engineer_pat_key

    def _build_openhands_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        # Persist OpenHands CLI settings under WORK_ROOT by overriding HOME.
        env["HOME"] = str(self._openhands_home_dir)

        # Pass through common provider keys.
        if self._settings.openai_api_key is not None:
            env["OPENAI_API_KEY"] = self._settings.openai_api_key
        if self._settings.openai_base_url is not None:
            env["OPENAI_BASE_URL"] = self._settings.openai_base_url
        if self._settings.openai_model is not None:
            env["OPENAI_MODEL"] = self._settings.openai_model

        if self._settings.llm_api_key is not None:
            env["LLM_API_KEY"] = self._settings.llm_api_key
        if self._settings.llm_base_url is not None:
            env["LLM_BASE_URL"] = self._settings.llm_base_url
        if self._settings.llm_model is not None:
            env["LLM_MODEL"] = self._settings.llm_model

        # Gemini / Google
        if self._settings.gemini_api_key is not None:
            env["GEMINI_API_KEY"] = self._settings.gemini_api_key
        if self._settings.google_api_key is not None:
            env["GOOGLE_API_KEY"] = self._settings.google_api_key

        return env


def create_app(*, work_root: str | None = None) -> FastAPI:
    """Creates FastAPI app."""

    logging.basicConfig(level=logging.INFO)
    settings = AppSettings()
    runtime = WorkerRuntime(settings=settings, work_root=work_root)

    app = FastAPI()
    app.state.runtime = runtime
    app.state.uvicorn_server = None

    @app.on_event("startup")
    async def _startup() -> None:
        await runtime.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        runtime._github_client.close()
        await runtime.stop()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/event")
    async def event(req: EventRequest) -> dict[str, object]:
        # Resolve context from payload or settings.
        payload = req.payload
        settings_repo = settings.repo
        settings_issue_number = settings.issue_number
        settings_base_branch = settings.base_branch

        event_obj = WorkerEvent(
            type=req.type,
            repo=payload.repo or settings_repo,
            issue_number=payload.issue_number or settings_issue_number,
            base_branch=payload.base_branch or settings_base_branch,
        )
        # For initial start, repo/issue_number must be provided either via payload or env.
        if not runtime._state_store.paths.state_file.exists():
            if event_obj.repo is None or event_obj.issue_number is None:
                raise HTTPException(
                    status_code=400,
                    detail="repo and issue_number are required for start.",
                )

        enqueue_result = await runtime.enqueue(event=event_obj)
        return {
            "queued": enqueue_result.queued,
            "queue_size": enqueue_result.queue_size,
        }

    @app.post("/stop")
    async def stop() -> dict[str, str]:
        await runtime.stop()
        server: uvicorn.Server | None = app.state.uvicorn_server
        if server is not None:
            server.should_exit = True
        return {"status": "stopping"}

    return app


async def _serve() -> None:
    settings = AppSettings()
    app = create_app()
    config = uvicorn.Config(
        app, host=settings.listen_host, port=settings.listen_port, log_level="info"
    )
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server
    await server.serve()


def main() -> None:
    """Entry point used by Docker CMD."""

    asyncio.run(_serve())


if __name__ == "__main__":
    main()

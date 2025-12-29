"""Startup validation for required credentials and configuration.

This module validates that all required credentials are present and valid
before the worker starts processing events.
"""

from __future__ import annotations

import shlex

from app.core.config import AppSettings
from app.integrations.git.git_ops import GitCommandError, GitOps, GitOpsConfig
from app.integrations.github.github_client import (
    GitHubApiError,
    GitHubClient,
    GitHubClientConfig,
)


class ValidationError(RuntimeError):
    """Raised when validation fails."""


def validate_github_token(*, token: str, api_base_url: str) -> None:
    """Validates GitHub token by making an API call.

    Args:
        token: GitHub token to validate.
        api_base_url: GitHub API base URL.

    Raises:
        ValidationError: If token is invalid or lacks required permissions.
    """
    client = GitHubClient(config=GitHubClientConfig(api_base_url=api_base_url, token=token))
    try:
        # Try to get the authenticated user to verify token validity.
        # This endpoint requires minimal permissions.
        client.verify_authentication()
    except GitHubApiError as exc:
        if exc.status_code == 401:
            raise ValidationError(
                "GitHub token is invalid or expired. "
                "Please verify that GITHUB_TOKEN (or ENGINEER_PAT_KEY) is correct."
            ) from exc
        if exc.status_code == 403:
            raise ValidationError(
                "GitHub token lacks required permissions. "
                "Please ensure the token has 'repo' scope."
            ) from exc
        raise ValidationError(
            f"GitHub API error: status={exc.status_code}, message={exc.message}"
        ) from exc
    finally:
        client.close()


def validate_github_git_access(*, token: str) -> None:
    """Validates GitHub token format for Git operations.

    Note: We skip actual Git operations during startup validation as they are
    resource-intensive. GitHub API authentication success is sufficient to assume
    Git operations will work. Actual Git authentication will be validated during
    the first clone/push operation.

    Args:
        token: GitHub token to validate (format only).

    Raises:
        ValidationError: If token format is obviously invalid.
    """
    # Basic validation: token should not be empty and should look like a token
    if not token or len(token.strip()) < 10:
        raise ValidationError(
            "GitHub token appears to be invalid (too short). "
            "Please verify that GITHUB_TOKEN (or ENGINEER_PAT_KEY) is set correctly."
        )


def validate_github_repo_push_permission(*, token: str, api_base_url: str, repo: str) -> None:
    """Validates that the token has push permission for the given repository.

    Args:
        token: GitHub token to validate.
        api_base_url: GitHub API base URL.
        repo: Repository in "owner/repo" format.

    Raises:
        ValidationError: If token cannot access the repository or lacks push permission.
    """
    client = GitHubClient(config=GitHubClientConfig(api_base_url=api_base_url, token=token))
    try:
        can_push = client.get_repository_push_permission(repo=repo)
        if not can_push:
            raise ValidationError(
                "GitHub token does not have push permission to the repository. " f"repo={repo}"
            )
    except GitHubApiError as exc:
        if exc.status_code == 401:
            raise ValidationError(
                "GitHub token is invalid or expired for repository access. " f"repo={repo}"
            ) from exc
        if exc.status_code in {403, 404}:
            raise ValidationError(
                "GitHub token cannot access the repository or lacks permissions. " f"repo={repo}"
            ) from exc
        raise ValidationError(
            f"GitHub API error while checking repository permission: status={exc.status_code}"
        ) from exc
    finally:
        client.close()


def validate_github_git_remote_access(*, token: str, repo: str) -> None:
    """Validates GitHub token can authenticate to repository via Git HTTPS.

    Args:
        token: GitHub token to validate.
        repo: Repository in "owner/repo" format.

    Raises:
        ValidationError: If Git HTTPS authentication fails.
    """
    git_ops = GitOps(
        config=GitOpsConfig(
            author_name="swe-worker-validation",
            author_email="validation@example.com",
        )
    )
    try:
        git_ops.verify_remote_access(repo=repo, github_token=token)
    except GitCommandError as exc:
        stderr = (exc.stderr or "").lower()
        if "invalid credentials" in stderr or "authentication failed" in stderr:
            raise ValidationError(
                "GitHub token authentication failed for Git HTTPS operations. " f"repo={repo}"
            ) from exc
        if "permission" in stderr or "denied" in stderr or "403" in stderr:
            raise ValidationError(
                "GitHub token lacks permission for Git HTTPS operations. " f"repo={repo}"
            ) from exc
        raise ValidationError(f"Git HTTPS validation failed. repo={repo}") from exc


def validate_llm_configuration(*, settings: AppSettings) -> None:
    """Validates LLM configuration is complete.

    Args:
        settings: Application settings.

    Raises:
        ValidationError: If LLM configuration is incomplete or invalid.
    """
    llm_model = settings.llm_model
    openai_model = settings.openai_model

    # Determine which model is being used
    model_provider: str | None = None
    model_name: str | None = None

    if llm_model:
        # Format: provider/model (e.g., "openai/gpt-4", "gemini/gemini-pro")
        parts = llm_model.split("/", 1)
        if len(parts) == 2:
            model_provider = parts[0].lower()
            model_name = parts[1]
        else:
            raise ValidationError(
                "LLM_MODEL format is invalid. Expected 'provider/model' "
                f"(e.g., 'openai/gpt-4'), got: {llm_model}"
            )
    elif openai_model:
        # Fallback to OpenAI if OPENAI_MODEL is set
        model_provider = "openai"
        model_name = openai_model
    else:
        raise ValidationError(
            "LLM configuration is missing. Please set either LLM_MODEL (e.g., 'openai/gpt-4') "
            "or OPENAI_MODEL environment variable."
        )

    # Validate provider-specific requirements
    if model_provider == "openai":
        if not settings.openai_api_key:
            raise ValidationError(
                f"LLM_MODEL is set to '{llm_model or f'openai/{openai_model}'}' "
                "but OPENAI_API_KEY is not set."
            )
        validate_openai_key_and_model(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model_name=model_name,
        )
    elif model_provider == "gemini":
        if not settings.google_api_key and not settings.gemini_api_key:
            raise ValidationError(
                f"LLM_MODEL is set to '{llm_model}' "
                "but neither GOOGLE_API_KEY nor GEMINI_API_KEY is set."
            )
    else:
        raise ValidationError(
            f"Unsupported LLM provider: {model_provider}. Supported providers are: openai, gemini"
        )


def validate_openai_key_and_model(
    *,
    api_key: str,
    base_url: str | None,
    model_name: str | None,
) -> None:
    """Validates OpenAI API key and model early to fail fast.

    This performs a minimal OpenAI API request. If the API key is invalid or the
    requested model is not accessible, OpenHands will spin on retries and appear
    "stuck" while no usage is recorded.

    Args:
        api_key: OpenAI API key.
        base_url: Optional OpenAI base URL override (e.g., proxy). If None, uses
            https://api.openai.com.
        model_name: Model name without provider prefix, e.g. "gpt-4o-mini".

    Raises:
        ValidationError: If authentication fails or model is not accessible.
    """
    import httpx

    api_base = (base_url or "https://api.openai.com").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(10.0)
    with httpx.Client(timeout=timeout, headers=headers) as client:
        # Authentication check.
        resp = client.get(f"{api_base}/v1/models")
        if resp.status_code == 401:
            raise ValidationError("OPENAI_API_KEY is invalid (OpenAI /v1/models returned 401).")
        if resp.status_code >= 400:
            raise ValidationError(
                f"OpenAI API key validation failed: status={resp.status_code}, body={resp.text[:300]}"
            )

        # Model access check (best-effort).
        if model_name:
            resp2 = client.get(f"{api_base}/v1/models/{model_name}")
            if resp2.status_code == 404:
                raise ValidationError(
                    f"OpenAI model is not available: model={model_name} (OpenAI returned 404)."
                )
            if resp2.status_code == 401:
                raise ValidationError(
                    f"OPENAI_API_KEY is invalid for model access: model={model_name} (401)."
                )
            if resp2.status_code >= 400:
                raise ValidationError(
                    f"OpenAI model validation failed: model={model_name} status={resp2.status_code} "
                    f"body={resp2.text[:300]}"
                )


def validate_openhands_command(*, command_line: str) -> None:
    """Validates that OpenHands command is configured and runnable.

    Args:
        command_line: Command line string (may include arguments), e.g. "uv run openhands".

    Raises:
        ValidationError: If OpenHands command is not runnable.
    """
    from app.integrations.process.subprocess_utils import (
        CommandRunner,
    )  # local import to keep module lightweight

    runner = CommandRunner()
    # Best-effort: call `--version` to ensure the command exists and can run.
    # Parse as argv to avoid shell injection and to support arguments.
    base_args = list(shlex.split(command_line))
    if not base_args:
        raise ValidationError("OPENHANDS_COMMAND must not be empty.")
    result = runner.run(args=[*base_args, "--version"])
    if result.exit_code != 0:
        raise ValidationError(
            "OpenHands command is not runnable. Please verify OPENHANDS_COMMAND and dependencies. "
            f"command={command_line}"
        )


def validate_all(*, settings: AppSettings) -> None:
    """Validates all required credentials and configuration.

    Args:
        settings: Application settings.

    Raises:
        ValidationError: If any validation fails.
    """
    errors: list[str] = []

    # Validate GitHub token
    github_token = settings.github_token or settings.engineer_pat_key
    if not github_token:
        errors.append("GITHUB_TOKEN (or ENGINEER_PAT_KEY) is required but not set.")
    else:
        try:
            validate_github_token(token=github_token, api_base_url=settings.github_api_base_url)
        except ValidationError as exc:
            errors.append(f"GitHub token validation failed: {exc}")

        # Validate Git token format (only if token is valid)
        if not errors:
            try:
                validate_github_git_access(token=github_token)
            except ValidationError as exc:
                errors.append(f"GitHub token format validation failed: {exc}")

        # If REPO is provided via env, validate repo permissions and Git HTTPS auth at startup.
        if not errors and settings.repo is not None:
            try:
                validate_github_repo_push_permission(
                    token=github_token,
                    api_base_url=settings.github_api_base_url,
                    repo=settings.repo,
                )
                validate_github_git_remote_access(token=github_token, repo=settings.repo)
            except ValidationError as exc:
                errors.append(f"GitHub repository access validation failed: {exc}")

    # OpenHands is required for normal operation.
    if settings.openhands_command is None:
        errors.append("OPENHANDS_COMMAND is required but not set.")
    else:
        try:
            validate_openhands_command(command_line=settings.openhands_command)
        except ValidationError as exc:
            errors.append(f"OpenHands command validation failed: {exc}")
        try:
            validate_llm_configuration(settings=settings)
        except ValidationError as exc:
            errors.append(f"LLM configuration validation failed: {exc}")

    if errors:
        error_message = "Startup validation failed:\n\n" + "\n".join(f"  - {err}" for err in errors)
        raise ValidationError(error_message)



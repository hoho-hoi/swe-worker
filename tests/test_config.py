from __future__ import annotations

from pathlib import Path

from app.core.config import AppSettings


def test_app_settings_can_load_from_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ENGINEER_PAT_KEY=pat_from_env_file",
                "OPENAI_API_KEY=openai_from_env_file",
                "GEMINI_API_KEY=gemini_from_env_file",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = AppSettings(_env_file=str(env_file))
    assert settings.engineer_pat_key == "pat_from_env_file"
    assert settings.openai_api_key == "openai_from_env_file"
    assert settings.gemini_api_key == "gemini_from_env_file"


def test_app_settings_strips_surrounding_quotes_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ENGINEER_PAT_KEY", '"quoted_pat"')
    monkeypatch.setenv("OPENAI_API_KEY", "'quoted_openai'")
    settings = AppSettings()
    assert settings.engineer_pat_key == "quoted_pat"
    assert settings.openai_api_key == "quoted_openai"

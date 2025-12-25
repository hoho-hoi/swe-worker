from __future__ import annotations

from pathlib import Path

from app.config import AppSettings


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

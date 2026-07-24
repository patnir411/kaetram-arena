"""Create-only tests for the local game database configuration."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.configure_local_game_env import (
    DOTENV,
    ConfigurationError,
    configure,
)


def test_configuration_is_exact_and_create_only(tmp_path: Path) -> None:
    (tmp_path / ".env.defaults").write_text("SKIP_DATABASE=true\n")
    path = configure(tmp_path)
    assert path.read_text() == DOTENV
    assert "127.0.0.1" in DOTENV
    assert "MONGODB_DATABASE=kaetram_devlopment" in DOTENV
    assert "MONGODB_USER=\n" in DOTENV
    with pytest.raises(ConfigurationError, match="overwrite"):
        configure(tmp_path)


def test_configuration_rejects_symlinked_game_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / ".env.defaults").write_text("SKIP_DATABASE=true\n")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="symlinked"):
        configure(link)

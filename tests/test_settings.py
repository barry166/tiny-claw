from __future__ import annotations

from tiny_claw._internal.settings import Settings


def test_settings_uses_current_directory_as_default_workdir() -> None:
    settings = Settings.from_env({})

    assert settings.workdir.is_absolute()
    assert settings.workdir.exists()


def test_settings_reads_workdir_from_environment(tmp_path) -> None:
    settings = Settings.from_env({"TINY_CLAW_WORKDIR": str(tmp_path)})

    assert settings.workdir == tmp_path.resolve()

import json
import os
import stat
from pathlib import Path

import pytest

import arden.atomic_file as atomic_file
import arden.config as config_module
import arden.settings as settings_module
from arden.settings import SettingsError


@pytest.fixture
def settings_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(settings_module, "ARDEN_DIR", tmp_path)
    return tmp_path


def test_missing_settings_are_first_run_only_without_a_backup(settings_root: Path):
    assert settings_module.load_user_settings() == {}

    backup = settings_root / "settings.json.bak"
    backup.write_text("{}")
    with pytest.raises(SettingsError, match="manual recovery is available"):
        settings_module.load_user_settings()


@pytest.mark.parametrize(
    ("payload", "backup_payload"),
    [
        ("{", None),
        ("{", "{}"),
        ("[]", "{"),
        ('{"value": NaN}', "{}"),
    ],
)
def test_invalid_primary_settings_never_fall_back_to_backup(
    settings_root: Path,
    payload: str,
    backup_payload: str | None,
):
    primary = settings_root / "settings.json"
    backup = settings_root / "settings.json.bak"
    primary.write_text(payload)
    if backup_payload is not None:
        backup.write_text(backup_payload)
    before = {path: path.read_bytes() for path in (primary, backup) if path.exists()}

    with pytest.raises(SettingsError):
        settings_module.load_user_settings()

    assert {path: path.read_bytes() for path in before} == before


def test_unreadable_primary_settings_raise_without_using_backup(settings_root: Path, monkeypatch):
    primary = settings_root / "settings.json"
    backup = settings_root / "settings.json.bak"
    primary.write_text("{}")
    backup.write_text("{}")
    read_text = Path.read_text

    def fail_primary(path: Path, *args, **kwargs) -> str:
        if path == primary:
            raise OSError("read failed")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_primary)

    with pytest.raises(SettingsError, match="unreadable or invalid JSON"):
        settings_module.load_user_settings()


def test_save_is_private_atomic_and_preserves_the_previous_backup(settings_root: Path):
    primary = settings_root / "settings.json"
    primary.write_text('{"version":"old"}')

    settings_module.save_user_settings({"version": "new"})

    assert json.loads(primary.read_text()) == {"version": "new"}
    assert (settings_root / "settings.json.bak").read_text() == '{"version":"old"}'
    assert stat.S_IMODE(primary.stat().st_mode) == 0o600
    assert stat.S_IMODE((settings_root / "settings.json.bak").stat().st_mode) == 0o600
    assert list(settings_root.glob(".settings.json.*.tmp")) == []


def test_primary_replace_failure_leaves_the_previous_settings_exact(settings_root: Path, monkeypatch):
    primary = settings_root / "settings.json"
    primary.write_text('{"version":"old"}')
    original = primary.read_bytes()
    replace = os.replace

    def fail_primary(source, destination) -> None:
        if Path(destination) == primary:
            raise OSError("replace failed")
        replace(source, destination)

    monkeypatch.setattr(atomic_file.os, "replace", fail_primary)

    with pytest.raises(OSError, match="replace failed"):
        settings_module.save_user_settings({"version": "new"})

    assert primary.read_bytes() == original
    assert (settings_root / "settings.json.bak").read_bytes() == original
    assert list(settings_root.glob(".*.tmp")) == []


def test_backup_failure_leaves_the_primary_untouched(settings_root: Path, monkeypatch):
    primary = settings_root / "settings.json"
    backup = settings_root / "settings.json.bak"
    primary.write_text('{"version":"old"}')
    backup.write_text("previous backup")
    primary_before = primary.read_bytes()
    backup_before = backup.read_bytes()
    replace = os.replace

    def fail_backup(source, destination) -> None:
        if Path(destination) == backup:
            raise OSError("backup failed")
        replace(source, destination)

    monkeypatch.setattr(atomic_file.os, "replace", fail_backup)

    with pytest.raises(OSError, match="backup failed"):
        settings_module.save_user_settings({"version": "new"})

    assert primary.read_bytes() == primary_before
    assert backup.read_bytes() == backup_before
    assert list(settings_root.glob(".*.tmp")) == []


def test_get_config_propagates_settings_corruption(monkeypatch):
    error = SettingsError("settings corrupt")
    monkeypatch.setattr(config_module, "load_custom_models", lambda _root: None)

    def fail_load() -> dict:
        raise error

    monkeypatch.setattr(config_module, "load_user_settings", fail_load)

    with pytest.raises(SettingsError) as raised:
        config_module.get_config()

    assert raised.value is error


def test_configured_default_models_must_exist(monkeypatch):
    monkeypatch.setattr(config_module, "get_models", lambda: {})

    with pytest.raises(RuntimeError, match="missing configured defaults"):
        config_module._validate_default_models()

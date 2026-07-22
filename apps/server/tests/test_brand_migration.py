from pathlib import Path

from arden.brand_migration import migrate_legacy_data_dir, promote_legacy_environment, resolve_data_dir


def test_resolve_data_dir_accepts_legacy_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ARDEN_DIR", raising=False)
    monkeypatch.setenv("NTRP_DIR", str(tmp_path))

    assert resolve_data_dir() == tmp_path


def test_arden_environment_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("NTRP_HOST", "legacy.example")
    monkeypatch.setenv("ARDEN_HOST", "arden.example")

    promote_legacy_environment()

    assert __import__("os").environ["ARDEN_HOST"] == "arden.example"


def test_promotes_legacy_environment(monkeypatch) -> None:
    monkeypatch.delenv("ARDEN_PORT", raising=False)
    monkeypatch.setenv("NTRP_PORT", "7000")

    promote_legacy_environment()

    assert __import__("os").environ["ARDEN_PORT"] == "7000"


def test_migrates_default_data_and_vault_metadata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    legacy = tmp_path / ".ntrp"
    (legacy / "memory" / ".ntrp").mkdir(parents=True)
    (legacy / "settings.json").write_text('{"memory": true}')
    (legacy / "memory" / ".ntrp" / "canonical-revision").write_text("abc")
    target = tmp_path / ".arden"

    assert migrate_legacy_data_dir(target) is True
    assert (target / "settings.json").read_text() == '{"memory": true}'
    assert (target / "memory" / ".arden" / "canonical-revision").read_text() == "abc"
    assert not (target / "memory" / ".ntrp").exists()
    assert (legacy / "settings.json").exists()


def test_does_not_overwrite_existing_arden_data(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    legacy = tmp_path / ".ntrp"
    target = tmp_path / ".arden"
    legacy.mkdir()
    target.mkdir()
    (legacy / "settings.json").write_text("legacy")
    (target / "settings.json").write_text("arden")

    assert migrate_legacy_data_dir(target) is False
    assert (target / "settings.json").read_text() == "arden"


def test_read_only_home_does_not_block_startup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".ntrp").mkdir()
    monkeypatch.setattr(
        "arden.brand_migration.shutil.copytree", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError())
    )

    assert migrate_legacy_data_dir(tmp_path / ".arden") is False


def test_locked_vault_metadata_does_not_block_startup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    target = tmp_path / ".arden"
    (target / "memory" / ".ntrp").mkdir(parents=True)
    monkeypatch.setattr(Path, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError()))

    assert migrate_legacy_data_dir(target) is False
    assert (target / "memory" / ".ntrp").exists()

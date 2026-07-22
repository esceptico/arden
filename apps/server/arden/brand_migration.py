"""One-time compatibility helpers for the Arden rename."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

LEGACY_ENV_PREFIX = "NTRP_"
LEGACY_DATA_DIR_NAME = ".ntrp"
ARDEN_ENV_PREFIX = "ARDEN_"
ARDEN_DATA_DIR_NAME = ".arden"


def resolve_data_dir() -> Path:
    configured = os.environ.get("ARDEN_DIR") or os.environ.get("NTRP_DIR")
    return Path(configured) if configured else Path.home() / ARDEN_DATA_DIR_NAME


def promote_legacy_environment() -> None:
    """Let old environment configuration work while Arden names take precedence."""
    for key, value in tuple(os.environ.items()):
        if not key.startswith(LEGACY_ENV_PREFIX):
            continue
        arden_key = f"{ARDEN_ENV_PREFIX}{key.removeprefix(LEGACY_ENV_PREFIX)}"
        os.environ.setdefault(arden_key, value)


def migrate_legacy_data_dir(target: Path) -> bool:
    """Copy the old default data directory once, preserving it as a rollback copy."""
    target = Path(target)
    legacy = Path.home() / LEGACY_DATA_DIR_NAME
    migrated = False

    if target != legacy and legacy.is_dir() and not target.exists():
        try:
            shutil.copytree(legacy, target, symlinks=True)
        except OSError:
            return False
        migrated = True

    _migrate_vault_metadata(target / "memory")
    return migrated


def _migrate_vault_metadata(vault: Path) -> None:
    legacy = vault / LEGACY_DATA_DIR_NAME
    current = vault / ARDEN_DATA_DIR_NAME
    if legacy.is_dir() and not current.exists():
        try:
            legacy.replace(current)
        except OSError:
            pass

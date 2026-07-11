import os
import tempfile
from pathlib import Path, PurePosixPath


def normalize_area_page_path(page_path: str) -> str:
    """The ONE page_path validity rule — storage and resolution both use it,
    so the DB can never hold a path the filesystem layer rejects."""
    raw = page_path.strip().replace("\\", "/")
    relative = PurePosixPath(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".md":
        raise ValueError("page_path must be a vault-relative Markdown path")
    return relative.as_posix()


def atomic_write_text(path: Path, content: str) -> None:
    """Durable atomic file replace shared by area page tools and stores."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_area_page(vault_root: Path, page_path: str) -> Path:
    relative = PurePosixPath(normalize_area_page_path(page_path))
    root = vault_root.resolve()
    candidate = (root / relative.as_posix()).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError("Area page escapes the memory vault")
    return candidate

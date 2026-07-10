from pathlib import Path, PurePosixPath


def resolve_area_page(vault_root: Path, page_path: str) -> Path:
    raw = page_path.strip().replace("\\", "/")
    relative = PurePosixPath(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".md":
        raise ValueError("page_path must be a vault-relative Markdown path")
    root = vault_root.resolve()
    candidate = (root / relative.as_posix()).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError("Area page escapes the memory vault")
    return candidate

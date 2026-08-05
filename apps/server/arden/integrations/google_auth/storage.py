import os
from pathlib import Path
from uuid import uuid4


def write_private_bytes(path: Path, content: bytes) -> None:
    """Atomically replace one secret file with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_private_text(path: Path, content: str) -> None:
    write_private_bytes(path, content.encode())

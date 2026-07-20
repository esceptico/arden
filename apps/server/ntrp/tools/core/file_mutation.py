import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

ABSENT_REVISION = "absent"


@dataclass(frozen=True, slots=True)
class FileRevision:
    sha256: str
    size: int


class RevisionConflict(Exception):
    def __init__(self, *, expected: str, observed: str):
        self.expected = expected
        self.observed = observed
        super().__init__(f"expected revision {expected}, observed {observed}")


def file_revision(path: Path) -> FileRevision:
    return read_file_snapshot(path)[1]


def read_file_snapshot(path: Path) -> tuple[bytes, FileRevision]:
    content = path.read_bytes()
    return content, FileRevision(sha256=sha256(content).hexdigest(), size=len(content))


def revision_or_absent(path: Path) -> str:
    try:
        return file_revision(path).sha256
    except FileNotFoundError:
        return ABSENT_REVISION


def _assert_revision(path: Path, expected_sha256: str) -> None:
    observed = revision_or_absent(path)
    if observed != expected_sha256:
        raise RevisionConflict(expected=expected_sha256, observed=observed)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_compare_and_swap(path: Path, content: str, expected_sha256: str) -> FileRevision:
    """Atomically replace *path* only when its content matches the expected revision."""
    _assert_revision(path, expected_sha256)
    encoded = content.encode("utf-8")
    temp_path = path.with_name(f".{path.name}.ntrp-{uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temp_path, flags, 0o666)
    try:
        if path.exists():
            os.fchmod(fd, path.stat().st_mode & 0o777)
        with os.fdopen(fd, "wb") as temp_file:
            fd = -1
            temp_file.write(encoded)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        _assert_revision(path, expected_sha256)
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return FileRevision(sha256=sha256(encoded).hexdigest(), size=len(encoded))

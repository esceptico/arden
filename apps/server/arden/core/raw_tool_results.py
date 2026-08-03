"""Content-addressed raw tool-result storage.

The hot SQLite event log stores a bounded preview and a stable manifest id.
The exact raw body lives here as compressed bytes keyed by sha256 so duplicate
payloads share one object and old manifests can be garbage-collected later.
"""

import gzip
import hashlib
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from arden.constants import RAW_TOOL_RESULT_DATA_KEY, RAW_TOOL_RESULT_PREVIEW_CHARS
from arden.settings import ARDEN_DIR

RAW_TOOL_RESULTS_BASE = ARDEN_DIR / "blobs" / "tool-results"
_COMPRESSION = "gzip"
_BLOB_FILE_LOCK = threading.Lock()


@dataclass(frozen=True)
class RawToolResultBlob:
    blob_ref: str
    blob_path: str
    content_sha256: str
    content_bytes: int
    stored_bytes: int
    compression: str = _COMPRESSION

    def to_internal_data(self) -> dict:
        return {
            RAW_TOOL_RESULT_DATA_KEY: {
                "blob_ref": self.blob_ref,
                "blob_path": self.blob_path,
                "content_sha256": self.content_sha256,
                "content_bytes": self.content_bytes,
                "stored_bytes": self.stored_bytes,
                "compression": self.compression,
            }
        }


def _ensure_ignore_marker() -> None:
    marker = RAW_TOOL_RESULTS_BASE / ".ignore"
    if not marker.exists():
        RAW_TOOL_RESULTS_BASE.mkdir(parents=True, exist_ok=True)
        marker.write_text("*\n", encoding="utf-8")


def _blob_path(content_sha256: str) -> Path:
    return RAW_TOOL_RESULTS_BASE / content_sha256[:2] / f"{content_sha256}.txt.gz"


def preview_text(content: str, *, limit: int = RAW_TOOL_RESULT_PREVIEW_CHARS) -> str:
    if len(content) <= limit:
        return content
    head = limit * 3 // 5
    tail = limit - head
    return f"{content[:head]}\n... [truncated raw tool result] ...\n{content[-tail:]}"


def persist_raw_tool_result(content: str) -> RawToolResultBlob:
    raw = content.encode("utf-8")
    content_sha256 = hashlib.sha256(raw).hexdigest()
    path = _blob_path(content_sha256)
    _ensure_ignore_marker()
    path.parent.mkdir(parents=True, exist_ok=True)

    with _BLOB_FILE_LOCK:
        if not path.exists():
            compressed = gzip.compress(raw)
            tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
            tmp.write_bytes(compressed)
            try:
                tmp.replace(path)
            except FileExistsError:
                tmp.unlink(missing_ok=True)
        else:
            # A concurrent orphan sweep uses mtime as its grace lease. Touching
            # an existing deduplicated blob prevents a stale inventory from
            # deleting it between persistence and manifest insertion.
            path.touch()

    return RawToolResultBlob(
        blob_ref=f"sha256:{content_sha256}",
        blob_path=str(path),
        content_sha256=content_sha256,
        content_bytes=len(raw),
        stored_bytes=path.stat().st_size,
    )


def read_raw_tool_result(blob_path: str, *, compression: str = _COMPRESSION) -> str:
    raw = Path(blob_path).read_bytes()
    if compression == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def read_raw_tool_result_by_ref(blob_ref: str) -> str:
    """Read a blob by its `sha256:<hex>` ref — the path is content-derived."""
    return read_raw_tool_result(str(_blob_path(blob_ref.removeprefix("sha256:"))))


def delete_stale_raw_tool_result(
    path: Path,
    *,
    blob_root: Path,
    older_than_timestamp: float,
    expected_size: int,
) -> bool:
    """Delete one still-stale regular blob under the allowlisted root.

    The shared lock closes the inventory→unlink race with
    :func:`persist_raw_tool_result`; callers must separately prove that no
    durable manifest references the blob.
    """

    with _BLOB_FILE_LOCK:
        try:
            metadata = path.lstat()
            path.resolve().relative_to(blob_root.resolve())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size != expected_size
                or metadata.st_mtime > older_than_timestamp
            ):
                return False
            path.unlink()
        except (FileNotFoundError, PermissionError, ValueError):
            return False
    return True


def internal_blob_from_data(data: dict | None) -> RawToolResultBlob | None:
    if not isinstance(data, dict):
        return None
    raw = data.get(RAW_TOOL_RESULT_DATA_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return RawToolResultBlob(
            blob_ref=str(raw["blob_ref"]),
            blob_path=str(raw["blob_path"]),
            content_sha256=str(raw["content_sha256"]),
            content_bytes=int(raw["content_bytes"]),
            stored_bytes=int(raw["stored_bytes"]),
            compression=str(raw.get("compression") or _COMPRESSION),
        )
    except (KeyError, TypeError, ValueError):
        return None


def strip_internal_raw_tool_result_data(data: dict | None) -> dict | None:
    if not isinstance(data, dict) or RAW_TOOL_RESULT_DATA_KEY not in data:
        return data
    cleaned = {k: v for k, v in data.items() if k != RAW_TOOL_RESULT_DATA_KEY}
    return cleaned or None

"""Full-fidelity, deterministic cold-session bundles."""

import base64
import gzip
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import zstandard

COLD_SESSION_FORMAT = "arden-cold-session-v1"


@dataclass(frozen=True)
class ColdBundleManifest:
    format_version: str
    session_id: str
    bundle_path: str
    bundle_sha256: str
    bundle_bytes: int
    message_count: int
    logical_bytes: int
    user_assistant_prose_sha256: str
    blob_hashes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raw_blob_digest(path: Path, compression: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    opener = gzip.open if compression == "gzip" else Path.open
    with opener(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _encode(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$arden_bytes": base64.b64encode(value).decode()}
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$arden_bytes"}:
        return base64.b64decode(value["$arden_bytes"], validate=True)
    if isinstance(value, dict):
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _prose_digest(snapshot: dict[str, Any]) -> str:
    prose: list[list[Any]] = []
    rows = snapshot.get("tables", {}).get("session_messages", [])
    for row in sorted(rows, key=lambda value: int(value.get("seq", 0))):
        if row.get("role") not in {"user", "assistant"}:
            continue
        try:
            message = json.loads(row.get("message_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            message = {"content": str(row.get("message_json") or "")}
        content = message.get("content")
        if isinstance(content, list):
            text = "\n".join(
                item if isinstance(item, str) else str(item.get("text") or "")
                for item in content
                if isinstance(item, (str, dict))
            )
        else:
            text = "" if content is None else str(content)
        prose.append([int(row.get("seq", 0)), str(row.get("role")), text])
    return _sha256(_canonical_json(prose))


def _logical_bytes(snapshot: dict[str, Any]) -> int:
    total = 0
    for row in snapshot.get("tables", {}).get("session_messages", []):
        total += len(str(row.get("message_json") or "").encode())
        total += len(str(row.get("search_text") or "").encode())
    for row in snapshot.get("tables", {}).get("session_events", []):
        total += len(str(row.get("event_json") or "").encode())
    session = snapshot.get("session", {})
    total += len(str(session.get("messages") or "").encode())
    return total


def _verified_blob(row: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    path = Path(str(row.get("blob_path") or ""))
    compression = str(row.get("compression") or "none")
    raw_sha256, raw_bytes = _raw_blob_digest(path, compression)
    content_sha256 = str(row.get("content_sha256") or "")
    if not content_sha256 or raw_sha256 != content_sha256:
        raise ValueError(f"tool-result blob integrity mismatch: {path}")
    expected_bytes = row.get("content_bytes")
    if expected_bytes is not None and int(expected_bytes) != raw_bytes:
        raise ValueError(f"tool-result blob length mismatch: {path}")
    return path, {
        "content_sha256": content_sha256,
        "compression": compression,
        "content_bytes": raw_bytes,
        "stored_bytes": path.stat().st_size,
        "stored_sha256": _sha256_file(path),
        "member": f"blobs/{content_sha256}.bin",
    }


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o600
    return info


def write_cold_session_bundle(snapshot: dict[str, Any], output_path: Path) -> ColdBundleManifest:
    """Write and verify one self-contained tar+zstd session bundle."""

    session_id = str(snapshot.get("session", {}).get("session_id") or "")
    if not session_id:
        raise ValueError("cold snapshot has no session_id")
    rows = snapshot.get("tables", {}).get("session_messages", [])

    blob_sources: dict[str, Path] = {}
    blob_manifest: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("tables", {}).get("tool_results", []):
        digest = str(row.get("content_sha256") or "")
        if not digest or digest in blob_sources:
            continue
        source, metadata = _verified_blob(row)
        blob_sources[digest] = source
        blob_manifest[digest] = metadata

    document = {
        "format_version": COLD_SESSION_FORMAT,
        "snapshot": _encode(snapshot),
        "blobs": [blob_manifest[digest] for digest in sorted(blob_manifest)],
    }
    payload = _canonical_json(document) + b"\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tar_fd, tar_name = tempfile.mkstemp(prefix=".cold-session-", suffix=".tar", dir=output_path.parent)
    os.close(tar_fd)
    tar_path = Path(tar_name)
    compressed_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with tarfile.open(tar_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
            import io

            archive.addfile(_tar_info("snapshot.json", len(payload)), io.BytesIO(payload))
            for digest in sorted(blob_sources):
                source = blob_sources[digest]
                with source.open("rb") as stream:
                    archive.addfile(_tar_info(f"blobs/{digest}.bin", source.stat().st_size), stream)
        compressor = zstandard.ZstdCompressor(level=10, write_checksum=True, write_content_size=True)
        with tar_path.open("rb") as source, compressed_path.open("wb") as destination:
            with compressor.stream_writer(destination, closefd=False) as writer:
                shutil.copyfileobj(source, writer, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        compressed_path.replace(output_path)
    finally:
        tar_path.unlink(missing_ok=True)
        compressed_path.unlink(missing_ok=True)

    manifest = ColdBundleManifest(
        format_version=COLD_SESSION_FORMAT,
        session_id=session_id,
        bundle_path=str(output_path.resolve()),
        bundle_sha256=_sha256_file(output_path),
        bundle_bytes=output_path.stat().st_size,
        message_count=len(rows),
        logical_bytes=_logical_bytes(snapshot),
        user_assistant_prose_sha256=_prose_digest(snapshot),
        blob_hashes=tuple(sorted(blob_manifest)),
    )
    restored = read_cold_session_bundle(output_path, verify_manifest=manifest)
    if _prose_digest(restored) != manifest.user_assistant_prose_sha256:
        raise ValueError("cold bundle prose parity failed")
    return manifest


def read_cold_session_bundle(
    path: Path,
    *,
    blob_root: Path | None = None,
    restore_blobs: bool = False,
    verify_manifest: ColdBundleManifest | None = None,
) -> dict[str, Any]:
    """Read without extracting paths; optionally restore verified blob bytes."""

    if verify_manifest is not None and _sha256_file(path) != verify_manifest.bundle_sha256:
        raise ValueError("cold bundle hash mismatch")
    tar_fd, tar_name = tempfile.mkstemp(prefix=".cold-session-read-", suffix=".tar", dir=path.parent)
    os.close(tar_fd)
    tar_path = Path(tar_name)
    try:
        decompressor = zstandard.ZstdDecompressor()
        with (
            path.open("rb") as source,
            tar_path.open("wb") as destination,
            decompressor.stream_reader(source) as reader,
        ):
            shutil.copyfileobj(reader, destination, length=1024 * 1024)
        with tarfile.open(tar_path, mode="r") as archive:
            names = {member.name for member in archive.getmembers() if member.isfile()}
            snapshot_member = archive.extractfile("snapshot.json")
            if snapshot_member is None:
                raise ValueError("cold bundle has no snapshot.json")
            document = json.loads(snapshot_member.read())
            if document.get("format_version") != COLD_SESSION_FORMAT:
                raise ValueError("unsupported cold session format")
            snapshot = _decode(document.get("snapshot"))
            if not isinstance(snapshot, dict):
                raise ValueError("invalid cold session snapshot")
            blob_locations: dict[str, str] = {}
            for metadata in document.get("blobs", []):
                member_name = str(metadata.get("member") or "")
                digest = str(metadata.get("content_sha256") or "")
                if member_name not in names or member_name != f"blobs/{digest}.bin":
                    raise ValueError("invalid cold bundle blob member")
                member = archive.extractfile(member_name)
                if member is None:
                    raise ValueError("cold bundle blob is unreadable")
                blob_fd, blob_name = tempfile.mkstemp(prefix=".cold-blob-", dir=path.parent)
                os.close(blob_fd)
                temporary_blob = Path(blob_name)
                try:
                    with temporary_blob.open("wb") as destination_stream:
                        shutil.copyfileobj(member, destination_stream, length=1024 * 1024)
                    if _sha256_file(temporary_blob) != metadata.get("stored_sha256"):
                        raise ValueError(f"cold bundle stored blob hash mismatch: {digest}")
                    raw_sha256, raw_bytes = _raw_blob_digest(
                        temporary_blob,
                        str(metadata.get("compression") or "none"),
                    )
                    if raw_sha256 != digest or raw_bytes != int(metadata.get("content_bytes", -1)):
                        raise ValueError(f"cold bundle raw blob integrity mismatch: {digest}")
                    if restore_blobs:
                        if blob_root is None:
                            raise ValueError("blob_root is required when restoring blobs")
                        suffix = ".txt.gz" if metadata.get("compression") == "gzip" else ".txt"
                        destination = blob_root / digest[:2] / f"{digest}{suffix}"
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        if destination.exists() and _sha256_file(destination) != _sha256_file(temporary_blob):
                            raise ValueError(f"existing content-addressed blob mismatch: {destination}")
                        if not destination.exists():
                            temporary_blob.replace(destination)
                        blob_locations[digest] = str(destination.resolve())
                finally:
                    temporary_blob.unlink(missing_ok=True)
            if restore_blobs:
                for row in snapshot.get("tables", {}).get("tool_results", []):
                    digest = str(row.get("content_sha256") or "")
                    if digest in blob_locations:
                        row["blob_path"] = blob_locations[digest]
                        row["blob_ref"] = f"sha256:{digest}"
            if verify_manifest is not None:
                session_id = str(snapshot.get("session", {}).get("session_id") or "")
                if session_id != verify_manifest.session_id:
                    raise ValueError("cold bundle session mismatch")
                if _prose_digest(snapshot) != verify_manifest.user_assistant_prose_sha256:
                    raise ValueError("cold bundle prose mismatch")
                if len(snapshot.get("tables", {}).get("session_messages", [])) != verify_manifest.message_count:
                    raise ValueError("cold bundle message count mismatch")
            return snapshot
    finally:
        tar_path.unlink(missing_ok=True)

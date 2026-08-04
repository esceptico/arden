import gzip
import hashlib
from pathlib import Path

from arden.trajectory.cold_storage import read_cold_session_bundle, write_cold_session_bundle


def test_cold_bundle_is_deterministic_self_contained_and_restorable(tmp_path: Path):
    raw = b"large tool evidence" * 10_000
    digest = hashlib.sha256(raw).hexdigest()
    source = tmp_path / "source" / f"{digest}.txt.gz"
    source.parent.mkdir()
    source.write_bytes(gzip.compress(raw, mtime=0))
    snapshot = {
        "session": {
            "session_id": "session-cold",
            "messages": "[]",
            "storage_state": "hot",
            "archived_at": "2026-08-01T00:00:00+00:00",
        },
        "tables": {
            "session_messages": [
                {
                    "session_id": "session-cold",
                    "message_id": "m1",
                    "seq": 0,
                    "role": "user",
                    "message_json": '{"role":"user","content":"keep me"}',
                    "search_text": "keep me",
                }
            ],
            "tool_results": [
                {
                    "session_id": "session-cold",
                    "content_sha256": digest,
                    "content_bytes": len(raw),
                    "stored_bytes": source.stat().st_size,
                    "compression": "gzip",
                    "blob_ref": f"sha256:{digest}",
                    "blob_path": str(source),
                }
            ],
        },
    }

    first = write_cold_session_bundle(snapshot, tmp_path / "first.tar.zst")
    second = write_cold_session_bundle(snapshot, tmp_path / "second.tar.zst")

    assert first.bundle_sha256 == second.bundle_sha256
    source.unlink()
    restored = read_cold_session_bundle(
        Path(first.bundle_path),
        blob_root=tmp_path / "restored-blobs",
        restore_blobs=True,
        verify_manifest=first,
    )
    restored_path = Path(restored["tables"]["tool_results"][0]["blob_path"])
    assert gzip.decompress(restored_path.read_bytes()) == raw
    assert restored["tables"]["session_messages"][0]["search_text"] == "keep me"


def test_cold_bundle_supports_empty_transcripts(tmp_path: Path):
    snapshot = {
        "session": {"session_id": "session-empty", "messages": "[]"},
        "tables": {"session_messages": [], "tool_results": []},
    }

    manifest = write_cold_session_bundle(snapshot, tmp_path / "empty.tar.zst")
    restored = read_cold_session_bundle(Path(manifest.bundle_path), verify_manifest=manifest)

    assert manifest.message_count == 0
    assert restored == snapshot

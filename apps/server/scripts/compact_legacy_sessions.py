from __future__ import annotations

import argparse
import json
from pathlib import Path

from arden.maintenance.session_compaction import compact_legacy_database


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline, lossless migration and compaction for legacy Arden session databases."
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("--blob-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="Build a verified compacted copy; do not replace the source.")
    mode.add_argument("--apply", action="store_true", help="Atomically swap after verification and keep a rollback DB.")
    parser.add_argument("--yes", action="store_true", help="Required with --apply.")
    parser.add_argument("--event-keep", type=int, default=10_000)
    parser.add_argument(
        "--archived-event-keep",
        type=int,
        help="Tighter replay-tail retention for archived sessions (default: --event-keep).",
    )
    parser.add_argument("--inline-limit", type=int, default=64 * 1024)
    parser.add_argument("--outbox-retention-days", type=int, default=1)
    parser.add_argument(
        "--allow-existing-missing-blobs",
        action="store_true",
        help="Preserve and report pre-existing broken blob refs instead of aborting.",
    )
    args = parser.parse_args()
    if args.apply and not args.yes:
        parser.error("--apply requires --yes")
    manifest = compact_legacy_database(
        args.database,
        blob_root=args.blob_root,
        output=args.output,
        apply=args.apply,
        event_keep=args.event_keep,
        archived_event_keep=args.archived_event_keep,
        inline_limit=args.inline_limit,
        outbox_retention_days=args.outbox_retention_days,
        allow_existing_missing_blobs=args.allow_existing_missing_blobs,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

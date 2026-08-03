from __future__ import annotations

import argparse
import json
from pathlib import Path

from arden.trajectory.export import export_cold_bundle, verify_cold_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Export immutable Arden transcripts as compressed cold sidecars.")
    parser.add_argument("database", type=Path, help="Path to sessions.db (opened read-only).")
    parser.add_argument("session_id")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    manifest = export_cold_bundle(args.database, args.session_id, args.output_dir)
    verify_cold_bundle(args.output_dir, manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

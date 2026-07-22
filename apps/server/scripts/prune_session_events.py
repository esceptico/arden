"""One-time maintenance: reclaim the durable session_events log.

The server stopped persisting token-delta events (TEXT_MESSAGE_CONTENT,
TOOL_CALL_ARGS, REASONING_MESSAGE_CONTENT) — they are ephemeral transport and
the final content is recoverable from terminal events. This script deletes the
historical delta rows that accumulated before that change and VACUUMs to
reclaim disk (SQLite does not shrink the file on DELETE alone).

Usage:
    uv run python scripts/prune_session_events.py [--db PATH] [--dry-run] [--no-vacuum]

Defaults to the configured sessions DB. Run it while the server is stopped (or
during low activity): VACUUM takes an exclusive lock and briefly needs free
disk roughly equal to the live data size.
"""

import argparse
import sqlite3
from pathlib import Path

from arden.config import get_config
from arden.events.sse import EPHEMERAL_EVENT_TYPES

EPHEMERAL_VALUES = sorted(t.value for t in EPHEMERAL_EVENT_TYPES)


def _human(n_bytes: int) -> str:
    gb = n_bytes / 1_073_741_824
    return f"{gb:.2f} GB" if gb >= 1 else f"{n_bytes / 1_048_576:.1f} MB"


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune ephemeral delta events from session_events and VACUUM.")
    parser.add_argument("--db", type=Path, default=None, help="sessions DB path (defaults to configured path)")
    parser.add_argument("--dry-run", action="store_true", help="report what would be deleted, change nothing")
    parser.add_argument("--no-vacuum", action="store_true", help="delete rows but skip VACUUM (no disk reclaim)")
    args = parser.parse_args()

    db_path = args.db or get_config().sessions_db_path
    print(f"sessions DB: {db_path}")
    print(f"file size:   {_human(Path(db_path).stat().st_size)}")
    print(f"ephemeral types targeted: {', '.join(EPHEMERAL_VALUES)}\n")

    conn = sqlite3.connect(str(db_path))
    try:
        placeholders = ", ".join("?" for _ in EPHEMERAL_VALUES)
        total = conn.execute("SELECT COUNT(*) FROM session_events").fetchone()[0]
        target = conn.execute(
            f"SELECT COUNT(*) FROM session_events WHERE event_type IN ({placeholders})",
            EPHEMERAL_VALUES,
        ).fetchone()[0]
        print(f"session_events rows: {total:,} total, {target:,} ephemeral ({target / total:.0%})" if total else "session_events empty")

        if args.dry_run:
            print("\n[dry-run] no changes made.")
            return
        if not target:
            print("\nnothing to delete.")
            return

        cur = conn.execute(
            f"DELETE FROM session_events WHERE event_type IN ({placeholders})",
            EPHEMERAL_VALUES,
        )
        conn.commit()
        print(f"\ndeleted {cur.rowcount:,} rows.")

        if not args.no_vacuum:
            print("running VACUUM (this can take a while on a large DB)...")
            conn.execute("VACUUM")
            conn.commit()
            print(f"VACUUM done. new file size: {_human(Path(db_path).stat().st_size)}")
        else:
            print("skipped VACUUM (--no-vacuum); disk not reclaimed until a later VACUUM.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

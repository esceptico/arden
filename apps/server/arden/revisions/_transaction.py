"""Prepared materialization and crash recovery for revision commits."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

from ._codec import (
    Transaction,
    TransactionRow,
    parse_commit,
    parse_transaction,
    parse_tree,
    transaction_record,
    validate_commit_transition,
)
from ._materialized import MaterializedFiles
from ._storage import (
    HistoryStorage,
    canonical_jsonl,
    decode_jsonl,
    sha256,
    valid_hash,
)
from .errors import CorruptRepositoryError, RevisionConflictError, UnsafePathError
from .models import ResourceState, ResourceVersion

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path


class TransactionManager:
    def __init__(
        self,
        storage: HistoryStorage,
        files: MaterializedFiles,
        checkpoint: Callable[[str], None],
    ) -> None:
        self.storage = storage
        self.files = files
        self.checkpoint = checkpoint

    def prepare(
        self,
        *,
        old_head: str | None,
        new_head: str,
        tree_id: str,
        key_hash: str,
        request_hash: str,
        rows: Iterable[TransactionRow],
    ) -> Transaction:
        ordered_rows = tuple(sorted(rows, key=lambda row: row.path))
        record = transaction_record(
            old_head=old_head,
            new_head=new_head,
            tree_id=tree_id,
            key_hash=key_hash,
            request_hash=request_hash,
            rows=ordered_rows,
        )
        manifest = canonical_jsonl(record)
        transaction_id = sha256(manifest)
        transaction_path = self.storage.transactions / transaction_id
        if self.storage.metadata_exists(transaction_path):
            raise CorruptRepositoryError(f"transaction already exists: {transaction_id}")
        self.storage.ensure_metadata_directory(transaction_path)
        self.storage.fsync_dir(self.storage.transactions)
        for name in ("publish", "backups", "displaced", "rejected", "markers"):
            self.storage.ensure_metadata_directory(transaction_path / name)
        for index, row in enumerate(ordered_rows):
            if row.new_blob is not None:
                self.storage.atomic_write(
                    self._artifact(transaction_path, "publish", index),
                    self.storage.read_blob(row.new_blob),
                    no_replace=True,
                )
            if row.old_blob is not None:
                self.storage.atomic_write(
                    self._artifact(transaction_path, "backups", index),
                    self.storage.read_blob(row.old_blob),
                    no_replace=True,
                )
        self.storage.atomic_write(transaction_path / "manifest.jsonl", manifest, no_replace=True)
        self.checkpoint("before_prepared")
        self._write_marker(transaction_path, "PREPARED")
        self.storage.fsync_dir(transaction_path)
        self.storage.fsync_dir(self.storage.transactions)
        self.checkpoint("after_prepared")
        return parse_transaction(record, transaction_id=transaction_id)

    def finish(self, transaction: Transaction) -> None:
        transaction_path = self.storage.transactions / transaction.transaction_id
        resulting = self._preflight(transaction_path, transaction)
        try:
            for index, row in enumerate(transaction.rows):
                if row.old_blob is not None:
                    self.files.displace(
                        row.path,
                        self._artifact(transaction_path, "displaced", index),
                        row.old_blob,
                    )
                else:
                    self.files.assert_state(row.path, None)
                self._write_marker(transaction_path, f"DISPLACED.{index:04d}")
                self.checkpoint(f"after_displaced:{index}")
            for index, row in enumerate(transaction.rows):
                if row.new_blob is not None:
                    self.files.install(
                        self._artifact(transaction_path, "publish", index),
                        row.path,
                        row.new_blob,
                    )
                self._write_marker(transaction_path, f"INSTALLED.{index:04d}")
                self.checkpoint(f"after_installed:{index}")
            self._assert_materialized(transaction, use_new=True, allow_transaction_links=True)
            self._assert_resource_tree(resulting, allow_transaction_links=True)
            self.checkpoint("before_decision")
        except (RevisionConflictError, UnsafePathError):
            if not self._has_marker(transaction_path, "DECIDED"):
                self._rollback(transaction_path, transaction)
            raise
        self._complete_decided(transaction_path, transaction)

    def recover(self) -> tuple[str, ...]:
        self.storage.cleanup_temporary_files()
        recovered: list[str] = []
        for transaction_path in self._transaction_paths():
            transaction_id = transaction_path.name
            if not self._has_marker(transaction_path, "PREPARED"):
                self.storage.remove_tree(transaction_path)
                recovered.append(transaction_id)
                continue
            transaction = self._load(transaction_path)
            self._validate_transaction_semantics(transaction)
            current = self.storage.read_ref()
            if (
                self._has_marker(transaction_path, "DECIDED")
                or self._has_marker(transaction_path, "COMMITTED")
                or current == transaction.new_head
            ):
                self._complete_decided(transaction_path, transaction)
            else:
                if current != transaction.old_head:
                    raise CorruptRepositoryError(
                        f"transaction {transaction_id} cannot recover from unexpected ref {current!r}"
                    )
                self._rollback(transaction_path, transaction)
            recovered.append(transaction_id)
        return tuple(recovered)

    def load_protected_transactions(self) -> tuple[Transaction, ...]:
        protected: list[Transaction] = []
        for parent in (self.storage.transactions, self.storage.conflicts):
            for path in self.storage.list_metadata(parent):
                self._validate_transaction_directory(path)
                if not self._has_marker(path, "PREPARED"):
                    continue
                transaction = self._load(path)
                self._validate_transaction_semantics(transaction)
                protected.append(transaction)
        return tuple(protected)

    def _preflight(
        self,
        transaction_path: Path,
        transaction: Transaction,
    ) -> dict[str, ResourceVersion]:
        if self.storage.read_ref() != transaction.old_head:
            raise RevisionConflictError("current ref changed before materialization")
        previous, resulting = self._validate_transaction_semantics(transaction)
        self._assert_resource_tree(previous, allow_transaction_links=False)
        for index, row in enumerate(transaction.rows):
            self.files.assert_state(row.path, row.old_blob)
            self.files.prepare_parent(row.path)
            if row.old_blob is not None:
                backup_path = self._artifact(transaction_path, "backups", index)
                self.storage.validate_metadata_file(backup_path)
                backup = self.storage.read_metadata(backup_path)
                if sha256(backup) != row.old_blob:
                    raise CorruptRepositoryError(f"transaction backup is invalid: {row.path}")
            if row.new_blob is not None:
                publish = self._artifact(transaction_path, "publish", index)
                self.storage.validate_metadata_file(publish)
                if sha256(self.storage.read_metadata(publish)) != row.new_blob:
                    raise CorruptRepositoryError(f"transaction publish artifact is invalid: {row.path}")
        return resulting

    def _complete_decided(self, transaction_path: Path, transaction: Transaction) -> None:
        _, resulting = self._validate_transaction_semantics(transaction)
        current = self.storage.read_ref()
        decided = self._has_marker(transaction_path, "DECIDED")
        if current not in {transaction.old_head, transaction.new_head}:
            raise CorruptRepositoryError(
                f"decided transaction {transaction.transaction_id} found unexpected ref {current!r}"
            )
        try:
            self._assert_materialized(transaction, use_new=True, allow_transaction_links=True)
            self._assert_resource_tree(resulting, allow_transaction_links=True)
        except (RevisionConflictError, UnsafePathError):
            if current == transaction.new_head and not decided:
                self.storage.compensate_ref(transaction.new_head, transaction.old_head)
            raise
        if current == transaction.old_head:
            self.storage.validate_working_binding()
            self.storage.compare_and_swap_ref(transaction.old_head, transaction.new_head)
            try:
                self.storage.validate_working_binding()
            except UnsafePathError:
                self.storage.compensate_ref(transaction.new_head, transaction.old_head)
                raise
        else:
            try:
                self.storage.validate_working_binding()
            except UnsafePathError:
                if not decided:
                    self.storage.compensate_ref(transaction.new_head, transaction.old_head)
                raise
        self.checkpoint("after_ref")
        if not decided:
            self._write_marker(transaction_path, "DECIDED")
        self.checkpoint("after_decision")
        self.storage.write_idempotency(
            transaction.key_hash,
            transaction.request_hash,
            transaction.new_head,
        )
        self.checkpoint("after_idempotency")
        if not self._has_marker(transaction_path, "COMMITTED"):
            self._write_marker(transaction_path, "COMMITTED")
        self.checkpoint("after_committed")
        self.storage.remove_tree(transaction_path)
        self.checkpoint("after_cleanup")

    def _rollback(self, transaction_path: Path, transaction: Transaction) -> None:
        conflicted = False
        for index, row in reversed(tuple(enumerate(transaction.rows))):
            actual = self._working_hash(row.path)
            if row.new_blob is not None and actual == row.new_blob:
                if not self.files.quarantine_if_matches(
                    row.path,
                    self._artifact(transaction_path, "rejected", index),
                    row.new_blob,
                    missing_ok=True,
                ):
                    conflicted = True
                actual = self._working_hash(row.path)
            if row.old_blob is None:
                if actual is not None:
                    conflicted = True
                continue
            if actual == row.old_blob:
                continue
            if actual is not None:
                conflicted = True
                continue
            source = self._artifact(transaction_path, "displaced", index)
            try:
                displaced_exists = self.storage.metadata_exists(source)
            except UnsafePathError:
                displaced_exists = False
                conflicted = True
            if not displaced_exists:
                source = self._artifact(transaction_path, "backups", index)
            try:
                self.storage.validate_metadata_file(source)
                if sha256(self.storage.read_metadata(source)) != row.old_blob:
                    raise CorruptRepositoryError(f"transaction restore artifact is invalid: {row.path}")
                if not self.files.restore_if_absent(source, row.path):
                    conflicted = True
            except (RevisionConflictError, UnsafePathError):
                conflicted = True
        previous, _ = self._validate_transaction_semantics(transaction)
        try:
            self._assert_resource_tree(previous, allow_transaction_links=False)
        except (CorruptRepositoryError, RevisionConflictError, UnsafePathError):
            conflicted = True
        if conflicted:
            self._write_marker(transaction_path, "CONFLICT")
            self._move_to_conflicts(transaction_path)
            return
        self._assert_materialized(transaction, use_new=False, allow_transaction_links=True)
        self._write_marker(transaction_path, "ROLLED_BACK")
        self.storage.remove_tree(transaction_path)
        self._assert_materialized(transaction, use_new=False, allow_transaction_links=False)

    def _assert_materialized(
        self,
        transaction: Transaction,
        *,
        use_new: bool,
        allow_transaction_links: bool,
    ) -> None:
        for row in transaction.rows:
            expected = row.new_blob if use_new else row.old_blob
            actual = self.files.hash(
                row.path,
                require_standalone=not allow_transaction_links,
            )
            if actual != expected:
                state = "new" if use_new else "old"
                raise CorruptRepositoryError(
                    f"transaction {transaction.transaction_id} does not materialize its {state} tree at {row.path}"
                )

    def _load(self, transaction_path: Path) -> Transaction:
        self._validate_transaction_directory(transaction_path)
        for name in ("publish", "backups", "displaced", "rejected", "markers"):
            self.storage.validate_metadata_directory(transaction_path / name)
        manifest_path = transaction_path / "manifest.jsonl"
        self.storage.validate_metadata_file(manifest_path)
        content = self.storage.read_metadata(manifest_path)
        if sha256(content) != transaction_path.name:
            raise CorruptRepositoryError(f"transaction {transaction_path.name} failed its manifest hash")
        record = decode_jsonl(content, label=f"transaction {transaction_path.name}")
        return parse_transaction(record, transaction_id=transaction_path.name)

    def _validate_transaction_semantics(
        self,
        transaction: Transaction,
    ) -> tuple[dict[str, ResourceVersion], dict[str, ResourceVersion]]:
        commit = parse_commit(self.storage.read_commit(transaction.new_head), commit_id=transaction.new_head)
        if commit.parent_id != transaction.old_head or commit.tree_id != transaction.tree_id:
            raise CorruptRepositoryError(f"transaction {transaction.transaction_id} does not match its commit")
        after = parse_tree(
            self.storage.read_tree(transaction.tree_id),
            tree_id=transaction.tree_id,
        )
        if transaction.old_head is None:
            before = {}
        else:
            parent = parse_commit(
                self.storage.read_commit(transaction.old_head),
                commit_id=transaction.old_head,
            )
            before = parse_tree(
                self.storage.read_tree(parent.tree_id),
                tree_id=parent.tree_id,
            )
        validate_commit_transition(commit, before, after)
        old_paths = {
            version.path: version.blob_id for version in before.values() if version.state is ResourceState.ACTIVE
        }
        new_paths = {
            version.path: version.blob_id for version in after.values() if version.state is ResourceState.ACTIVE
        }
        expected_rows = tuple(
            TransactionRow(path=path, old_blob=old_paths.get(path), new_blob=new_paths.get(path))
            for path in sorted(set(old_paths) | set(new_paths))
            if old_paths.get(path) != new_paths.get(path)
        )
        if transaction.rows != expected_rows:
            raise CorruptRepositoryError(f"transaction {transaction.transaction_id} rows do not match its tree delta")
        return before, after

    def _assert_resource_tree(
        self,
        resources: dict[str, ResourceVersion],
        *,
        allow_transaction_links: bool,
    ) -> None:
        for resource in resources.values():
            if resource.state is not ResourceState.ACTIVE:
                continue
            actual = self.files.hash(
                resource.path,
                require_standalone=not allow_transaction_links,
            )
            if actual != resource.blob_id:
                raise RevisionConflictError(
                    f"current files do not match resource {resource.resource_id} at {resource.path}"
                )

    def _transaction_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for path in self.storage.list_metadata(self.storage.transactions):
            self._validate_transaction_directory(path)
            paths.append(path)
        return tuple(paths)

    def _validate_transaction_directory(self, path: Path) -> None:
        if not valid_hash(path.name):
            raise CorruptRepositoryError(f"invalid transaction directory name: {path.name}")
        path_stat = self.storage.metadata_stat(path)
        assert path_stat is not None
        if not stat.S_ISDIR(path_stat.st_mode):
            raise CorruptRepositoryError(f"transaction path is not a directory: {path.name}")

    def _working_hash(self, path: str) -> str | None:
        try:
            return self.files.hash(path, require_standalone=False)
        except (RevisionConflictError, UnsafePathError):
            return None

    def _move_to_conflicts(self, transaction_path: Path) -> None:
        target = self.storage.conflicts / transaction_path.name
        if self.storage.metadata_exists(target):
            raise CorruptRepositoryError(f"conflict transaction already exists: {transaction_path.name}")
        self.storage.rename_metadata_directory(transaction_path, target)
        self.storage.fsync_dir(self.storage.transactions)
        self.storage.fsync_dir(self.storage.conflicts)

    def _write_marker(self, transaction_path: Path, name: str) -> None:
        marker = transaction_path / name if name == "PREPARED" else transaction_path / "markers" / name
        if self._has_marker(transaction_path, name):
            return
        self.storage.atomic_write(marker, b"", no_replace=True)
        self.storage.fsync_dir(transaction_path)

    def _has_marker(self, transaction_path: Path, name: str) -> bool:
        marker = transaction_path / name if name == "PREPARED" else transaction_path / "markers" / name
        return self.storage.validate_metadata_file(marker, allow_missing=True)

    @staticmethod
    def _artifact(transaction_path: Path, kind: str, index: int) -> Path:
        return transaction_path / kind / f"{index:04d}"

"""Race-resistant, no-symlink access rooted at one POSIX directory."""

import errno
import hashlib
import os
import stat as stat_module
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import CDLL, c_char_p, c_int, c_uint, get_errno
from os import PathLike
from pathlib import Path, PurePosixPath
from uuid import uuid4

from arden.revisions.errors import CorruptRepositoryError, RevisionConflictError, UnsafePathError

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_REGULAR_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_LIBC = CDLL(None, use_errno=True)


def ensure_absolute_directory(path: str | PathLike[str]) -> Path:
    """Create an absolute directory path without trusting its parents."""
    root = canonical_absolute(path)
    descriptor = _open_absolute_directory(root, create=True)
    os.close(descriptor)
    return root


def canonical_absolute(path: str | PathLike[str]) -> Path:
    """Resolve existing aliases once, then use only the canonical absolute path."""
    return Path(os.path.realpath(os.path.abspath(os.fspath(path))))


class AnchoredDirectory:
    """Filesystem operations relative to one pinned, non-symlink directory."""

    def __init__(self, root: str | PathLike[str]) -> None:
        self.root = canonical_absolute(root)
        self._root_fd = -1
        self._root_fd = _open_absolute_directory(self.root, create=True)

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    def __enter__(self) -> "AnchoredDirectory":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def ensure_directory(self, rel: str | PathLike[str]) -> None:
        current = self._duplicate_root()
        try:
            for part in _parts(rel):
                try:
                    child = _open_directory(part, current)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, 0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(current)
                    child = _open_directory(part, current)
                os.close(current)
                current = child
        finally:
            os.close(current)

    def validate_directory(self, rel: str | PathLike[str] = ".") -> None:
        descriptor = self._open_directory(rel)
        os.close(descriptor)

    def validate_binding(self) -> None:
        try:
            current = _open_absolute_directory(self.root, create=False)
        except FileNotFoundError as exc:
            raise UnsafePathError(f"anchored directory is missing: {self.root}") from exc
        try:
            pinned_stat = os.fstat(self._root_fd)
            current_stat = os.fstat(current)
            if (pinned_stat.st_dev, pinned_stat.st_ino) != (
                current_stat.st_dev,
                current_stat.st_ino,
            ):
                raise UnsafePathError(f"anchored directory was replaced: {self.root}")
        finally:
            os.close(current)

    @contextmanager
    def open_parent(self, rel: str | PathLike[str], *, create: bool = False) -> Iterator[tuple[int, str]]:
        with self._open_parent(rel, create=create, validate_binding=True) as opened:
            yield opened

    @contextmanager
    def _open_parent(
        self,
        rel: str | PathLike[str],
        *,
        create: bool,
        validate_binding: bool,
    ) -> Iterator[tuple[int, str]]:
        parts = _parts(rel, allow_root=False)
        parent = self._duplicate_root(validate_binding=validate_binding)
        try:
            for part in parts[:-1]:
                try:
                    child = _open_directory(part, parent)
                except FileNotFoundError:
                    if not create:
                        raise CorruptRepositoryError(f"required directory is missing: {rel}") from None
                    try:
                        os.mkdir(part, 0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(parent)
                    child = _open_directory(part, parent)
                os.close(parent)
                parent = child
            yield parent, parts[-1]
        finally:
            os.close(parent)

    def read_file(
        self,
        rel: str | PathLike[str],
        *,
        allow_missing: bool = False,
        require_standalone: bool = False,
    ) -> bytes | None:
        try:
            with self.open_parent(rel) as (parent, leaf):
                try:
                    descriptor = _open_regular(
                        leaf,
                        parent,
                        missing_is_corrupt=not allow_missing,
                    )
                except FileNotFoundError:
                    return None
                try:
                    opened = os.fstat(descriptor)
                    if require_standalone and opened.st_nlink != 1:
                        raise UnsafePathError(f"file has unsupported hard links: {rel}")
                    with os.fdopen(descriptor, "rb") as stream:
                        descriptor = -1
                        return stream.read()
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
        except CorruptRepositoryError:
            if allow_missing:
                return None
            raise

    def stat(self, rel: str | PathLike[str], *, allow_missing: bool = False) -> os.stat_result | None:
        try:
            with self.open_parent(rel) as (parent, leaf):
                try:
                    descriptor = _open_regular(leaf, parent, missing_is_corrupt=not allow_missing)
                except FileNotFoundError:
                    return None
                try:
                    return os.fstat(descriptor)
                finally:
                    os.close(descriptor)
        except CorruptRepositoryError:
            if allow_missing:
                return None
            raise

    def entry_stat(
        self,
        rel: str | PathLike[str],
        *,
        allow_missing: bool = False,
    ) -> os.stat_result | None:
        try:
            with self.open_parent(rel) as (parent, leaf):
                try:
                    entry = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    if allow_missing:
                        return None
                    raise CorruptRepositoryError(f"required entry is missing: {rel}") from None
                if stat_module.S_ISLNK(entry.st_mode):
                    raise UnsafePathError(f"entry is a symlink: {rel}")
                if not (stat_module.S_ISREG(entry.st_mode) or stat_module.S_ISDIR(entry.st_mode)):
                    raise UnsafePathError(f"entry is not regular: {rel}")
                return entry
        except CorruptRepositoryError:
            if allow_missing:
                return None
            raise

    def exists(self, rel: str | PathLike[str]) -> bool:
        return self.stat(rel, allow_missing=True) is not None

    def list(self, rel: str | PathLike[str] = ".") -> tuple[str, ...]:
        descriptor = self._open_directory(rel)
        try:
            return tuple(sorted(os.listdir(descriptor)))
        finally:
            os.close(descriptor)

    def atomic_write(
        self,
        rel: str | PathLike[str],
        content: bytes,
        *,
        no_replace: bool = False,
        mode: int = 0o600,
    ) -> None:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        with self.open_parent(rel, create=True) as (parent, leaf):
            self._validate_replace_target(parent, leaf)
            temporary = f".{leaf}.{uuid4().hex}.tmp"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=parent,
            )
            try:
                os.fchmod(descriptor, mode)
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor = -1
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                if no_replace:
                    try:
                        os.link(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                    except FileExistsError as exc:
                        raise RevisionConflictError(f"target already exists: {rel}") from exc
                    os.unlink(temporary, dir_fd=parent)
                else:
                    self._validate_replace_target(parent, leaf)
                    os.replace(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def compare_and_swap_file_pinned(
        self,
        rel: str | PathLike[str],
        *,
        expected: bytes,
        replacement: bytes | None,
    ) -> None:
        """Compensate a ref write through the pinned root after its public path moved."""
        with self._open_parent(rel, create=False, validate_binding=False) as (parent, leaf):
            descriptor = _open_regular(leaf, parent, missing_is_corrupt=True)
            try:
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = -1
                    current = stream.read()
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if current != expected:
                raise RevisionConflictError(f"pinned file changed before compensation: {rel}")
            if replacement is None:
                os.unlink(leaf, dir_fd=parent)
                os.fsync(parent)
                return
            temporary = f".{leaf}.{uuid4().hex}.tmp"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor = -1
                    stream.write(replacement)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def write_immutable(self, rel: str | PathLike[str], content: bytes, expected_sha256: str) -> None:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_sha256:
            raise CorruptRepositoryError(f"immutable content hash does not match: {rel}")
        if self.exists(rel):
            if self.read_file(rel) != content:
                raise CorruptRepositoryError(f"immutable file differs: {rel}")
            return
        try:
            self.atomic_write(rel, content, no_replace=True, mode=0o444)
        except RevisionConflictError:
            if self.read_file(rel) != content:
                raise CorruptRepositoryError(f"immutable file differs: {rel}") from None

    def unlink(self, rel: str | PathLike[str], *, missing_ok: bool = False) -> None:
        with self.open_parent(rel) as (parent, leaf):
            try:
                _validate_regular_leaf(leaf, parent)
            except FileNotFoundError:
                if missing_ok:
                    return
                raise CorruptRepositoryError(f"required file is missing: {rel}") from None
            os.unlink(leaf, dir_fd=parent)
            os.fsync(parent)

    def rename_to(
        self,
        other: "AnchoredDirectory",
        source_rel: str | PathLike[str],
        target_rel: str | PathLike[str],
    ) -> None:
        with (
            self.open_parent(source_rel) as (source_parent, source_leaf),
            other.open_parent(target_rel, create=True) as (target_parent, target_leaf),
        ):
            _validate_regular_leaf(source_leaf, source_parent)
            try:
                _rename_no_replace(
                    source_parent,
                    source_leaf,
                    target_parent,
                    target_leaf,
                )
            except FileExistsError as exc:
                raise RevisionConflictError(f"target already exists: {target_rel}") from exc
            os.fsync(source_parent)
            if target_parent != source_parent:
                os.fsync(target_parent)

    def rename_directory_to(
        self,
        other: "AnchoredDirectory",
        source_rel: str | PathLike[str],
        target_rel: str | PathLike[str],
    ) -> None:
        with (
            self.open_parent(source_rel) as (source_parent, source_leaf),
            other.open_parent(target_rel, create=True) as (target_parent, target_leaf),
        ):
            source = os.stat(source_leaf, dir_fd=source_parent, follow_symlinks=False)
            if stat_module.S_ISLNK(source.st_mode) or not stat_module.S_ISDIR(source.st_mode):
                raise UnsafePathError(f"source is not a directory: {source_rel}")
            try:
                os.stat(target_leaf, dir_fd=target_parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise RevisionConflictError(f"target already exists: {target_rel}")
            try:
                _rename_no_replace(
                    source_parent,
                    source_leaf,
                    target_parent,
                    target_leaf,
                )
            except FileExistsError as exc:
                raise RevisionConflictError(f"target already exists: {target_rel}") from exc
            os.fsync(source_parent)
            if target_parent != source_parent:
                os.fsync(target_parent)

    def fsync_directory(self, rel: str | PathLike[str] = ".") -> None:
        descriptor = self._open_directory(rel)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def remove_tree(self, rel: str | PathLike[str]) -> None:
        with self.open_parent(rel) as (parent, leaf):
            _remove_directory(parent, leaf)
            os.fsync(parent)

    def device(self, rel: str | PathLike[str] = ".") -> int:
        descriptor = self._open_directory(rel)
        try:
            return os.fstat(descriptor).st_dev
        finally:
            os.close(descriptor)

    def open_lock(self, rel: str | PathLike[str]) -> int:
        with self.open_parent(rel, create=True) as (parent, leaf):
            descriptor = os.open(
                leaf,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=parent,
            )
            try:
                opened = os.fstat(descriptor)
                if not stat_module.S_ISREG(opened.st_mode):
                    raise UnsafePathError(f"lock is not a regular file: {rel}")
                os.fsync(parent)
                result = descriptor
                descriptor = -1
                return result
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    def _duplicate_root(self, *, validate_binding: bool = True) -> int:
        if self._root_fd < 0:
            raise UnsafePathError("anchored directory is closed")
        if validate_binding:
            self.validate_binding()
        return os.dup(self._root_fd)

    def _open_directory(self, rel: str | PathLike[str]) -> int:
        current = self._duplicate_root()
        try:
            for part in _parts(rel):
                child = _open_directory(part, current)
                os.close(current)
                current = child
            return current
        except FileNotFoundError as exc:
            raise CorruptRepositoryError(f"required directory is missing: {rel}") from exc
        except BaseException:
            os.close(current)
            raise

    @staticmethod
    def _validate_replace_target(parent: int, leaf: str) -> None:
        try:
            entry = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat_module.S_ISLNK(entry.st_mode):
            raise UnsafePathError(f"file is a symlink: {leaf}")
        if not stat_module.S_ISREG(entry.st_mode):
            raise UnsafePathError(f"file is not regular: {leaf}")


def _open_absolute_directory(path: Path, *, create: bool) -> int:
    current = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            try:
                child = _open_directory(part, current)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
                else:
                    os.fsync(current)
                child = _open_directory(part, current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _open_directory(name: str, parent: int) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR} and _is_symlink(name, parent):
            raise UnsafePathError(f"directory is a symlink: {name}") from exc
        if exc.errno == errno.ENOTDIR:
            raise UnsafePathError(f"path is not a directory: {name}") from exc
        raise


def _open_regular(name: str, parent: int, *, missing_is_corrupt: bool) -> int:
    try:
        descriptor = os.open(name, _REGULAR_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        if missing_is_corrupt:
            raise CorruptRepositoryError(f"required file is missing: {name}") from None
        raise
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafePathError(f"file is a symlink: {name}") from exc
        raise
    try:
        if not stat_module.S_ISREG(os.fstat(descriptor).st_mode):
            raise UnsafePathError(f"file is not regular: {name}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _validate_regular_leaf(name: str, parent: int) -> os.stat_result:
    entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat_module.S_ISLNK(entry.st_mode):
        raise UnsafePathError(f"file is a symlink: {name}")
    if not stat_module.S_ISREG(entry.st_mode):
        raise UnsafePathError(f"file is not regular: {name}")
    return entry


def _is_symlink(name: str, parent: int) -> bool:
    try:
        entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat_module.S_ISLNK(entry.st_mode)


def _remove_directory(parent: int, name: str) -> None:
    descriptor = _open_directory(name, parent)
    try:
        for child in os.listdir(descriptor):
            entry = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat_module.S_ISLNK(entry.st_mode):
                raise UnsafePathError(f"tree entry is a symlink: {child}")
            if stat_module.S_ISDIR(entry.st_mode):
                _remove_directory(descriptor, child)
            elif stat_module.S_ISREG(entry.st_mode):
                os.unlink(child, dir_fd=descriptor)
            else:
                raise UnsafePathError(f"tree entry is not a regular file: {child}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent)


def _rename_no_replace(
    source_parent: int,
    source_leaf: str,
    target_parent: int,
    target_leaf: str,
) -> None:
    source = os.fsencode(source_leaf)
    target = os.fsencode(target_leaf)
    if sys.platform == "darwin":
        operation = _LIBC.renameatx_np
        operation.argtypes = (c_int, c_char_p, c_int, c_char_p, c_uint)
        operation.restype = c_int
        result = operation(source_parent, source, target_parent, target, 0x00000004)
    elif sys.platform.startswith("linux"):
        operation = _LIBC.renameat2
        operation.argtypes = (c_int, c_char_p, c_int, c_char_p, c_uint)
        operation.restype = c_int
        result = operation(source_parent, source, target_parent, target, 0x00000001)
    else:
        raise UnsafePathError("atomic no-replace rename is unsupported on this platform")
    if result == 0:
        return
    error = get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error, os.strerror(error), target_leaf)
    raise OSError(error, os.strerror(error), source_leaf, target_leaf)


def _parts(rel: str | PathLike[str], *, allow_root: bool = True) -> tuple[str, ...]:
    value = os.fspath(rel)
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise UnsafePathError(f"path contains an unsafe character: {value!r}")
    if value == "." and allow_root:
        return ()
    parsed = PurePosixPath(value)
    if (
        not value
        or parsed.is_absolute()
        or value != parsed.as_posix()
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise UnsafePathError(f"path must be normalized and relative: {value!r}")
    return parsed.parts

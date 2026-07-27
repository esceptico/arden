"""Errors raised by the managed-file revision core."""


class RevisionError(RuntimeError):
    """Base error for revision repository failures."""


class RevisionConflictError(RevisionError):
    """A resource or working-tree precondition changed before publication."""


class IdempotencyConflictError(RevisionError):
    """An idempotency key was reused for a different request."""


class CorruptRepositoryError(RevisionError):
    """Canonical revision metadata or an immutable object is invalid."""


class UnsafePathError(RevisionError, ValueError):
    """A managed or metadata path is unsafe."""


class NoChangesError(RevisionError, ValueError):
    """A change set produces no new repository state."""

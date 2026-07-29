"""Public domain errors for snapshot-pinned wiki operations."""

from arden.revisions.errors import RevisionConflictError


class WikiValidationError(ValueError):
    """The visible wiki tree violates a domain invariant."""


class WikiAmbiguityError(WikiValidationError):
    """A page name can resolve to more than one page."""


class WikiSnapshotChangedError(RevisionConflictError):
    """A pinned wiki read became stale before it completed."""


class WikiRenameApplyAmbiguousError(RuntimeError):
    """A rename may have committed, but a coupled write did not confirm."""


class GeneratedRegionConflictError(WikiValidationError):
    """A user changed a producer-owned generated region."""


class WikiMaintenanceEvidenceLimitError(WikiValidationError):
    """Bounded scheduled evidence cannot safely include a whole commit."""

    def __init__(
        self,
        *,
        commit_id: str,
        resource_id: str,
        section: str,
        actual_bytes: int,
        limit_bytes: int,
        actual_bytes_at_least: bool,
        fingerprint: str,
    ) -> None:
        qualifier = "at least " if actual_bytes_at_least else ""
        super().__init__(
            f"commit {commit_id} resource {resource_id} {section} is {qualifier}{actual_bytes} UTF-8 bytes; "
            f"limit is {limit_bytes}"
        )
        self.commit_id = commit_id
        self.resource_id = resource_id
        self.section = section
        self.actual_bytes = actual_bytes
        self.limit_bytes = limit_bytes
        self.actual_bytes_at_least = actual_bytes_at_least
        self.fingerprint = fingerprint

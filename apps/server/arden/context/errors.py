class SessionSchemaError(RuntimeError):
    """The persisted context database is not a supported complete schema."""


class SessionDataCorruptionError(RuntimeError):
    """Persisted session data does not satisfy the current storage contract."""

"""History traversal and immutable resource diff rendering."""

import difflib
from collections.abc import Callable, Iterable, Mapping

from arden.revisions.errors import CorruptRepositoryError, RevisionContentLimitError
from arden.revisions.models import Commit, ResourceChange, ResourceDiff, ResourceDiffPage, ResourceVersion


def history(
    *,
    head: str | None,
    resource_id: str | None,
    start: str | None,
    stop_before: str | None,
    limit: int | None,
    valid_hash: Callable[[object], bool],
    require_reachable: Callable[[str], None],
    load_commit: Callable[[object], Commit],
    validate_commit_transition: Callable[[Commit], Mapping[str, ResourceVersion]],
) -> tuple[Commit, ...]:
    """Return newest-first commits from an already recovered repository snapshot."""

    if limit is not None and limit < 1:
        raise ValueError("history limit must be positive")
    if stop_before is not None and not valid_hash(stop_before):
        raise KeyError(f"unknown history stop boundary: {stop_before!r}")
    cursor = head if start is None else start
    if start is not None:
        require_reachable(start)
    commits: list[Commit] = []
    seen: set[str] = set()
    while cursor is not None:
        if cursor == stop_before:
            return tuple(commits)
        if cursor in seen:
            raise CorruptRepositoryError(f"commit history contains a cycle at {cursor}")
        seen.add(cursor)
        commit = load_commit(cursor)
        validate_commit_transition(commit)
        if resource_id is None or any(change_resource_id(change) == resource_id for change in commit.changes):
            if limit is None or len(commits) < limit:
                commits.append(commit)
            if stop_before is None and limit is not None and len(commits) >= limit:
                break
        cursor = commit.parent_id
    if stop_before is not None:
        raise KeyError(f"history stop boundary is not reachable from the selected start: {stop_before}")
    return tuple(commits)


def diff(
    *,
    before: Mapping[str, ResourceVersion],
    after: Mapping[str, ResourceVersion],
    resource_ids: Iterable[str] | None,
    read_blob: Callable[[str], bytes],
) -> tuple[ResourceDiff, ...]:
    """Render complete diffs between two explicit immutable tree snapshots."""

    selected: set[str] | None = None
    if resource_ids is not None:
        if isinstance(resource_ids, (str, bytes)):
            raise TypeError("resource_ids must be an iterable of resource IDs")
        selected = set(resource_ids)
        if not all(isinstance(resource_id, str) and resource_id for resource_id in selected):
            raise ValueError("resource_ids must contain only nonempty strings")
    result: list[ResourceDiff] = []
    changed_ids = set(before) | set(after)
    if selected is not None:
        changed_ids &= selected
    for resource_id in sorted(changed_ids):
        old = before.get(resource_id)
        new = after.get(resource_id)
        if old == new:
            continue
        old_content = b"" if old is None else read_blob(old.blob_id)
        new_content = b"" if new is None else read_blob(new.blob_id)
        old_label = "/dev/null" if old is None else old.path
        new_label = "/dev/null" if new is None else new.path
        result.append(
            ResourceDiff(
                resource_id=resource_id,
                before=old,
                after=new,
                unified_diff=unified_diff(old_content, new_content, old_label, new_label),
            )
        )
    return tuple(result)


def diff_page(
    *,
    before: ResourceVersion | None,
    after: ResourceVersion | None,
    resource_id: str,
    offset: int,
    limit: int,
    tail: bool,
    read_blob: Callable[[str], bytes],
) -> ResourceDiffPage:
    """Render one bounded page between explicit resource-version snapshots."""

    validate_page_arguments(resource_id=resource_id, offset=offset, limit=limit, tail=tail)
    if before == after:
        raise ValueError(f"resource did not change between revisions: {resource_id}")
    old_content = b"" if before is None else read_blob(before.blob_id)
    new_content = b"" if after is None else read_blob(after.blob_id)
    old_label = "/dev/null" if before is None else before.path
    new_label = "/dev/null" if after is None else after.path
    page_offset, page_end, has_more, text = unified_diff_page(
        old_content,
        new_content,
        old_label,
        new_label,
        offset=offset,
        limit=limit,
        tail=tail,
    )
    return ResourceDiffPage(
        resource_id=resource_id,
        before=before,
        after=after,
        offset=page_offset,
        end_offset=page_end,
        has_more=has_more,
        unified_diff=text,
    )


def diff_versions_page(
    *,
    before: ResourceVersion | None,
    after: ResourceVersion | None,
    offset: int,
    limit: int,
    tail: bool,
    source_byte_limit: int | None,
    blob_size: Callable[[str], int],
    read_blob: Callable[[str], bytes],
) -> ResourceDiffPage:
    """Page a diff between exact immutable versions without tree traversal."""

    for name, version in (("before", before), ("after", after)):
        if version is not None and not isinstance(version, ResourceVersion):
            raise TypeError(f"{name} must be a ResourceVersion or None")
    if before is not None and after is not None and before.resource_id != after.resource_id:
        raise ValueError("resource versions must have the same resource ID")
    if after is not None:
        resource = after
    elif before is not None:
        resource = before
    else:
        raise ValueError("a resource diff requires before or after")
    if before == after:
        raise ValueError(f"resource did not change between versions: {resource.resource_id}")
    validate_page_arguments(resource_id=resource.resource_id, offset=offset, limit=limit, tail=tail)
    if source_byte_limit is not None and (
        isinstance(source_byte_limit, bool) or not isinstance(source_byte_limit, int) or source_byte_limit <= 0
    ):
        raise ValueError("source_byte_limit must be a positive integer or None")

    versions = tuple(version for version in (before, after) if version is not None)
    unique = {version.blob_id: version for version in versions}
    actual_bytes = sum(blob_size(version.blob_id) for version in unique.values())
    if source_byte_limit is not None and actual_bytes > source_byte_limit:
        raise RevisionContentLimitError(actual_bytes=actual_bytes, limit_bytes=source_byte_limit)
    contents = {blob_id: read_blob(blob_id) for blob_id in unique}
    return diff_page(
        before=before,
        after=after,
        resource_id=resource.resource_id,
        offset=offset,
        limit=limit,
        tail=tail,
        read_blob=contents.__getitem__,
    )


def change_resource_id(change: ResourceChange) -> str:
    if change.after is not None:
        return change.after.resource_id
    if change.before is not None:
        return change.before.resource_id
    raise CorruptRepositoryError("resource change has no identity")


def validate_page_arguments(*, resource_id: str, offset: int, limit: int, tail: bool) -> None:
    if not isinstance(resource_id, str) or not resource_id:
        raise ValueError("resource_id must be a nonempty string")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if not isinstance(tail, bool):
        raise TypeError("tail must be a bool")
    if tail and offset != 0:
        raise ValueError("offset must be zero when tail is requested")


def unified_diff(before: bytes, after: bytes, old_label: str, new_label: str) -> str:
    return "".join(unified_diff_chunks(before, after, old_label, new_label))


def unified_diff_chunks(before: bytes, after: bytes, old_label: str, new_label: str):
    before_text = before.decode("utf-8", errors="surrogateescape").splitlines(keepends=True)
    after_text = after.decode("utf-8", errors="surrogateescape").splitlines(keepends=True)
    return difflib.unified_diff(
        before_text,
        after_text,
        fromfile=old_label,
        tofile=new_label,
        lineterm="\n",
    )


def unified_diff_page(
    before: bytes,
    after: bytes,
    old_label: str,
    new_label: str,
    *,
    offset: int,
    limit: int,
    tail: bool,
) -> tuple[int, int, bool, str]:
    chunks = unified_diff_chunks(before, after, old_label, new_label)
    if tail:
        text = ""
        total = 0
        for chunk in chunks:
            total += len(chunk)
            text = chunk[-limit:] if len(chunk) >= limit else (text + chunk)[-limit:]
        page_offset = 0 if total == 0 else ((total - 1) // limit) * limit
        page_length = total - page_offset
        return page_offset, total, False, text[-page_length:] if page_length else ""

    cursor = 0
    parts: list[str] = []
    length = 0
    has_more = False
    iterator = iter(chunks)
    for chunk in iterator:
        next_cursor = cursor + len(chunk)
        if next_cursor <= offset:
            cursor = next_cursor
            continue
        start = max(0, offset - cursor)
        available_length = len(chunk) - start
        remaining = limit - length
        taken = min(available_length, remaining)
        parts.append(chunk[start : start + taken])
        length += taken
        cursor = next_cursor
        if available_length > remaining:
            has_more = True
            break
        if length == limit:
            has_more = any(bool(next_chunk) for next_chunk in iterator)
            break
    else:
        if offset > cursor or (offset == cursor and cursor > 0 and length == 0):
            raise IndexError("offset is outside the resource diff")

    text = "".join(parts)
    return offset, offset + len(text), has_more, text

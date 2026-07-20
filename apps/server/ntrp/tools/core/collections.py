import base64
import binascii
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: tuple[T, ...]
    total: int
    has_more: bool
    next_cursor: str | None

    def to_dict(self) -> dict:
        return {
            "items": list(self.items),
            "total": self.total,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
        }


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode().rstrip("=")


def cursor_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
        prefix, value = raw.split(":", 1)
        if prefix != "offset":
            raise ValueError
        offset = int(value)
        if offset < 0:
            raise ValueError
        return offset
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise ValueError("Invalid pagination cursor") from exc


def paginate[T](items: list[T], *, limit: int, cursor: str | None = None) -> Page[T]:
    if limit < 1:
        raise ValueError("Pagination limit must be positive")
    offset = cursor_offset(cursor)
    selected = tuple(items[offset : offset + limit])
    next_offset = offset + len(selected)
    has_more = next_offset < len(items)
    return Page(
        items=selected,
        total=len(items),
        has_more=has_more,
        next_cursor=_cursor(next_offset) if has_more else None,
    )


def format_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Canonical timestamps require timezone-aware datetimes")
    return value.isoformat()

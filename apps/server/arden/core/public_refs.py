"""Deterministic, model-facing references for internal identities."""

import hashlib
import re
import unicodedata
from typing import Annotated

from pydantic import StringConstraints

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_SUFFIX_LENGTH = 6
_SUFFIX_SPACE = 36**_SUFFIX_LENGTH
PUBLIC_REF_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*~[a-z0-9]{6}$"
PublicRef = Annotated[str, StringConstraints(pattern=PUBLIC_REF_PATTERN)]
_PUBLIC_REF = re.compile(PUBLIC_REF_PATTERN)


def public_ref(
    text: str,
    identity: str,
    *,
    empty_slug: str,
    max_slug_length: int = 56,
) -> str:
    """Derive ``text-slug~6base36`` from immutable text and identity."""

    if not text or not identity:
        raise ValueError("public ref text and identity must be non-empty")
    slug = _slug(text, empty_slug=empty_slug, max_length=max_slug_length)
    digest = hashlib.sha256(f"{text}\0{identity}".encode()).digest()
    suffix = _base36(int.from_bytes(digest[:8], "big") % _SUFFIX_SPACE)
    return f"{slug}~{suffix}"


def is_public_ref(value: str, *, slug: str | None = None) -> bool:
    """Return whether ``value`` is one exact public-ref token."""

    if _PUBLIC_REF.fullmatch(value) is None:
        return False
    return slug is None or value.startswith(f"{slug}~")


def _slug(text: str, *, empty_slug: str, max_length: int) -> str:
    if max_length < 1:
        raise ValueError("max_slug_length must be positive")
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", empty_slug) is None:
        raise ValueError("empty_slug must already be a lowercase slug")
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:max_length].rstrip("-")
    return value or empty_slug


def _base36(value: int) -> str:
    result = ""
    for _ in range(_SUFFIX_LENGTH):
        value, digit = divmod(value, 36)
        result = _ALPHABET[digit] + result
    return result

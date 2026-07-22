"""YAML frontmatter for memory artifact files.

Mirrors Dex's small-frontmatter discipline: meta-fields live in a leading
``---`` block, the markdown body follows. Dates are stored as ISO strings and
forced to round-trip as strings (never PyYAML ``datetime``).
"""

from __future__ import annotations

import yaml

_FRONTMATTER_RE = "---\n"


class QuotedStr(str):
    """String that always serializes with double quotes (keeps iso dates as text)."""


def _quoted_representer(dumper: yaml.Dumper, data: QuotedStr) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


yaml.add_representer(QuotedStr, _quoted_representer, Dumper=yaml.SafeDumper)


def dump_frontmatter(meta: dict) -> str:
    body = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    return f"---\n{body}---\n"


def parse_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---\n"):
        return {}, content
    rest = content[len("---\n") :]
    end = rest.find("\n---\n")
    if end == -1:
        return {}, content
    raw = rest[:end]
    body = rest[end + len("\n---\n") :]
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return {}, content
    return data, body


def strip_frontmatter(content: str) -> str:
    return parse_frontmatter(content)[1]

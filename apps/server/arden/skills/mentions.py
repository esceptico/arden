"""Skill mentions: `/skill-name` anywhere in a user message loads the skill.

Codex-style expansion: the user's raw text is preserved verbatim, and each
mentioned skill's rendered body is injected as its own model-visible message
alongside it. Only exact registry names expand — `/Users/x` or an unknown
`/word` stays plain text. There is no argument mechanism: trailing text
simply remains in the message for the model to read.
"""

import re

from arden.skills.registry import SkillRegistry

# A mention starts the message or follows whitespace (never mid-path like
# foo/bar or http://x/y) and ends before any char outside the name charset.
_MENTION_RE = re.compile(r"(?:^|(?<=\s))/([a-z][a-z0-9-]{0,47})(?![\w/-])")


def find_skill_mentions(text: str, names: set[str]) -> list[str]:
    """Registry names mentioned as /name tokens, in order, deduped."""
    seen: list[str] = []
    for match in _MENTION_RE.finditer(text):
        name = match.group(1)
        if name in names and name not in seen:
            seen.append(name)
    return seen


def render_skill_mentions(text: str, registry: SkillRegistry) -> list[str]:
    """Rendered <skill> blocks for every skill mentioned in *text*."""
    blocks = []
    for name in find_skill_mentions(text, set(registry.names)):
        rendered = registry.render_skill_xml(name)
        if rendered is not None:
            blocks.append(rendered)
    return blocks

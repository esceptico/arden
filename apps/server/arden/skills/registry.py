import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from arden.logging import get_logger

_logger = get_logger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")


@dataclass
class SkillMeta:
    name: str
    description: str
    path: Path
    location: str
    source: str | None = None
    version: str | None = None
    reviewed_at: str | None = None
    # "skill" = a markdown playbook the agent reads + follows itself.
    # "workflow" = a runnable Orchestra preset; its dir holds a workflow.py the
    # `workflow` tool executes verbatim. Same registry, two flavors.
    kind: str = "skill"


@dataclass
class SkillValidationIssue:
    path: Path
    location: str
    reason: str
    detail: str


def _parse_skill_md(content: str) -> tuple[dict, str] | None:
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None
    try:
        frontmatter = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(frontmatter, dict):
        return None
    return frontmatter, content[m.end() :]


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, SkillMeta] = {}
        self._validation_issues: list[SkillValidationIssue] = []

    def load(self, dirs: list[tuple[Path, str]]) -> None:
        self._validation_issues.clear()
        for path, location in dirs:
            self._scan_dir(path, location)
        if self._skills:
            _logger.info("Loaded %d skill(s): %s", len(self._skills), ", ".join(self._skills))

    @property
    def validation_issues(self) -> list[dict[str, str]]:
        return [
            {
                "path": str(issue.path),
                "location": issue.location,
                "reason": issue.reason,
                "detail": issue.detail,
            }
            for issue in self._validation_issues
        ]

    def _record_issue(self, path: Path, location: str, reason: str, detail: str) -> None:
        self._validation_issues.append(SkillValidationIssue(path=path, location=location, reason=reason, detail=detail))

    def _scan_dir(self, base: Path, location: str) -> None:
        if not base.exists():
            return
        for skill_dir in sorted(base.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text()
            except OSError:
                _logger.warning("Failed to read %s", skill_md)
                continue
            parsed = _parse_skill_md(content)
            if not parsed:
                _logger.warning("Invalid frontmatter in %s", skill_md)
                continue
            frontmatter, _ = parsed
            name = frontmatter.get("name")
            description = frontmatter.get("description")
            if not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name):
                self._record_issue(
                    skill_md,
                    location,
                    "invalid_name",
                    "Skill name must match ^[a-z][a-z0-9-]{0,47}$.",
                )
                continue
            if skill_dir.name != name:
                self._record_issue(
                    skill_md,
                    location,
                    "directory_name_mismatch",
                    "Skill directory must match frontmatter name.",
                )
                continue
            if not isinstance(description, str) or not description.strip():
                self._record_issue(skill_md, location, "missing_description", "Skill description is required.")
                _logger.warning("Missing name or description in %s", skill_md)
                continue
            if name in self._skills:
                continue
            reviewed_at = _optional_date(frontmatter.get("reviewed_at"))
            if frontmatter.get("reviewed_at") is not None and reviewed_at is None:
                self._record_issue(
                    skill_md,
                    location,
                    "invalid_reviewed_at",
                    "reviewed_at must be an ISO date.",
                )
                continue
            self._skills[name] = SkillMeta(
                name=name,
                description=description.strip(),
                path=skill_dir,
                location=location,
                source=_optional_string(frontmatter.get("source")),
                version=_optional_string(frontmatter.get("version")),
                reviewed_at=reviewed_at,
                kind="workflow" if frontmatter.get("kind") == "workflow" else "skill",
            )

    def list_all(self) -> list[SkillMeta]:
        return list(self._skills.values())

    def get(self, name: str) -> SkillMeta | None:
        return self._skills.get(name)

    def load_body(self, name: str) -> str | None:
        meta = self._skills.get(name)
        if not meta:
            return None
        try:
            content = (meta.path / "SKILL.md").read_text()
        except OSError:
            return None
        parsed = _parse_skill_md(content)
        if not parsed:
            return None
        _, body = parsed
        return body.strip()

    def load_workflow_script(self, name: str) -> str | None:
        """The Orchestra script for a workflow-kind preset (its workflow.py),
        loaded verbatim. None if the name isn't a workflow preset or has no
        script file."""
        meta = self._skills.get(name)
        if not meta or meta.kind != "workflow":
            return None
        try:
            return (meta.path / "workflow.py").read_text()
        except OSError:
            return None

    def render_skill_xml(self, name: str, args: str = "", *, args_label: str = "ARGUMENTS") -> str | None:
        meta = self.get(name)
        body = self.load_body(name)
        if meta is None or body is None:
            return None
        body = body.replace("<skill_path>", str(meta.path))
        content = f'<skill name="{name}" path="{meta.path}">\n{body}\n</skill>'
        if args:
            content += f"\n\n{args_label}: {args}"
        return content

    def to_prompt_xml(self) -> str:
        if not self._skills:
            return ""
        lines = ["<available_skills>"]
        for meta in self._skills.values():
            lines.append("  <skill>")
            lines.append(f"    <name>{meta.name}</name>")
            lines.append(f"    <description>{meta.description}</description>")
            lines.append(f"    <location>{meta.location}</location>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)

    def remove(self, name: str) -> bool:
        meta = self._skills.get(name)
        if not meta:
            return False
        if meta.location == "builtin":
            _logger.warning("Cannot remove builtin skill '%s'", name)
            return False
        shutil.rmtree(meta.path, ignore_errors=True)
        del self._skills[name]
        _logger.info("Removed skill '%s'", name)
        return True

    def reload(self, dirs: list[tuple[Path, str]]) -> None:
        self._skills.clear()
        self.load(dirs)

    @property
    def names(self) -> list[str]:
        return list(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def __bool__(self) -> bool:
        return bool(self._skills)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool, date)):
        return str(value)
    return None


def _optional_date(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None

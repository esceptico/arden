# Skill Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add strict skill metadata validation and read-only stale-skill reporting with cleanup candidates only.

**Architecture:** Keep skill governance as inventory and reporting first. Skill loading should reject invalid metadata, the service should expose a read-only report, and cleanup should remain a candidate list until the user explicitly approves mutation through the existing remove path.

**Tech Stack:** Python dataclasses, existing `SkillRegistry` / `SkillService`, FastAPI router, pytest.

---

## File Structure

- Modify `apps/server/arden/skills/registry.py`: add validation issues, source/version/review metadata, and duplicate/invalid reporting.
- Modify `apps/server/arden/skills/service.py`: expose a read-only governance report.
- Modify `apps/server/arden/server/schemas.py`: add response schemas.
- Modify `apps/server/arden/server/routers/skills.py`: add `GET /skills/governance`.
- Modify `docs/guides/skills.mdx`: document required metadata and cleanup-candidate semantics.
- Test `apps/server/tests/test_skills.py`: validation and report behavior.

## Task 1: Validate Skill Metadata

**Files:**
- Modify: `apps/server/arden/skills/registry.py`
- Test: `apps/server/tests/test_skills.py`

- [ ] **Step 1: Write failing test**

Add:

```python
from pathlib import Path


def _write_skill(root: Path, name: str, frontmatter: str, body: str = "# Body\n") -> None:
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}---\n\n{body}")


def test_registry_rejects_invalid_skill_metadata(tmp_path):
    _write_skill(
        tmp_path,
        "Bad_Name",
        "name: Bad_Name\ndescription: works\n",
    )
    registry = SkillRegistry()

    registry.load([(tmp_path, "project")])

    assert registry.get("Bad_Name") is None
    assert registry.validation_issues[0]["reason"] == "invalid_name"
    assert registry.validation_issues[0]["path"].endswith("Bad_Name/SKILL.md")
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd apps/server && uv run pytest tests/test_skills.py::test_registry_rejects_invalid_skill_metadata -q
```

Expected: fail because `validation_issues` does not exist and name validation is not centralized in the registry.

- [ ] **Step 3: Implement validator**

Add to `apps/server/arden/skills/registry.py`:

```python
_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")


@dataclass
class SkillValidationIssue:
    path: Path
    location: str
    reason: str
    detail: str
```

Add fields:

```python
self._validation_issues: list[SkillValidationIssue] = []
```

Add property:

```python
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
```

Add helper:

```python
def _record_issue(self, path: Path, location: str, reason: str, detail: str) -> None:
    self._validation_issues.append(
        SkillValidationIssue(path=path, location=location, reason=reason, detail=detail)
    )
```

In `_scan_dir`, record and skip:

```python
if not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name):
    self._record_issue(skill_md, location, "invalid_name", "Skill name must match ^[a-z][a-z0-9-]{0,47}$.")
    continue
if skill_dir.name != name:
    self._record_issue(skill_md, location, "directory_name_mismatch", "Skill directory must match frontmatter name.")
    continue
if not isinstance(description, str) or not description.strip():
    self._record_issue(skill_md, location, "missing_description", "Skill description is required.")
    continue
```

Clear `_validation_issues` in `load(...)` before scanning.

- [ ] **Step 4: Verify green**

Run:

```bash
cd apps/server && uv run pytest tests/test_skills.py::test_registry_rejects_invalid_skill_metadata -q
```

Expected: pass.

## Task 2: Track Governance Metadata

**Files:**
- Modify: `apps/server/arden/skills/registry.py`
- Test: `apps/server/tests/test_skills.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_registry_loads_skill_governance_metadata(tmp_path):
    _write_skill(
        tmp_path,
        "research-helper",
        (
            "name: research-helper\n"
            "description: Helps with research\n"
            "source: github:example/research-helper\n"
            "version: 2026-05-16\n"
            "reviewed_at: 2026-05-16\n"
        ),
    )
    registry = SkillRegistry()

    registry.load([(tmp_path, "project")])

    skill = registry.get("research-helper")
    assert skill is not None
    assert skill.source == "github:example/research-helper"
    assert skill.version == "2026-05-16"
    assert skill.reviewed_at == "2026-05-16"
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd apps/server && uv run pytest tests/test_skills.py::test_registry_loads_skill_governance_metadata -q
```

Expected: fail because `SkillMeta` lacks these fields.

- [ ] **Step 3: Implement optional metadata**

Extend `SkillMeta`:

```python
source: str | None = None
version: str | None = None
reviewed_at: str | None = None
```

When creating `SkillMeta`, read optional string frontmatter values:

```python
source = frontmatter.get("source")
version = frontmatter.get("version")
reviewed_at = frontmatter.get("reviewed_at")
```

Store only values that are strings; otherwise use `None`.

- [ ] **Step 4: Verify green**

Run:

```bash
cd apps/server && uv run pytest tests/test_skills.py::test_registry_loads_skill_governance_metadata -q
```

Expected: pass.

## Task 3: Build Read-Only Stale Skill Report

**Files:**
- Modify: `apps/server/arden/skills/service.py`
- Test: `apps/server/tests/test_skills.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_skill_service_governance_report_marks_cleanup_candidates(tmp_path):
    _write_skill(
        tmp_path,
        "old-helper",
        (
            "name: old-helper\n"
            "description: Old helper\n"
            "reviewed_at: 2025-01-01\n"
        ),
    )
    registry = SkillRegistry()
    registry.load([(tmp_path, "project")])
    service = SkillService(registry)

    report = service.governance_report(now_date="2026-05-16")

    assert report["summary"]["cleanup_candidate_count"] == 1
    assert report["cleanup_candidates"][0]["name"] == "old-helper"
    assert report["cleanup_candidates"][0]["reason"] == "review_stale"
    assert registry.get("old-helper") is not None
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd apps/server && uv run pytest tests/test_skills.py::test_skill_service_governance_report_marks_cleanup_candidates -q
```

Expected: fail because `governance_report` does not exist.

- [ ] **Step 3: Implement report**

Add to `SkillService`:

```python
from datetime import date


def governance_report(self, *, now_date: str | None = None) -> dict:
    today = date.fromisoformat(now_date) if now_date else date.today()
    cleanup_candidates = []
    inventory = []
    for skill in self._registry.list_all():
        row = {
            "name": skill.name,
            "description": skill.description,
            "location": skill.location,
            "path": str(skill.path),
            "source": skill.source,
            "version": skill.version,
            "reviewed_at": skill.reviewed_at,
        }
        inventory.append(row)
        if skill.reviewed_at:
            reviewed = date.fromisoformat(skill.reviewed_at)
            if (today - reviewed).days >= 180:
                cleanup_candidates.append({**row, "reason": "review_stale"})

    return {
        "summary": {
            "skill_count": len(inventory),
            "validation_issue_count": len(self._registry.validation_issues),
            "cleanup_candidate_count": len(cleanup_candidates),
        },
        "inventory": inventory,
        "validation_issues": self._registry.validation_issues,
        "cleanup_candidates": cleanup_candidates,
    }
```

Registry validation must reject a non-ISO `reviewed_at` value with reason `invalid_reviewed_at`, so the report never has to parse malformed dates from loaded skills.

- [ ] **Step 4: Verify green**

Run:

```bash
cd apps/server && uv run pytest tests/test_skills.py::test_skill_service_governance_report_marks_cleanup_candidates -q
```

Expected: pass.

## Task 4: Expose Governance API

**Files:**
- Modify: `apps/server/arden/server/schemas.py`
- Modify: `apps/server/arden/server/routers/skills.py`
- Test: `apps/server/tests/test_skills.py`

- [ ] **Step 1: Write router-level test**

Add to `apps/server/tests/test_skills.py`:

```python
from fastapi.testclient import TestClient

from arden.server.app import app
from arden.server.deps import require_skill_service


def test_skill_governance_endpoint_returns_report(tmp_path):
    _write_skill(
        tmp_path,
        "old-helper",
        (
            "name: old-helper\n"
            "description: Old helper\n"
            "reviewed_at: 2025-01-01\n"
        ),
    )
    registry = SkillRegistry()
    registry.load([(tmp_path, "project")])
    service = SkillService(registry)
    app.dependency_overrides[require_skill_service] = lambda: service

    try:
        response = TestClient(app).get("/skills/governance")
    finally:
        app.dependency_overrides.pop(require_skill_service, None)

    assert response.status_code == 200
    assert response.json()["summary"]["cleanup_candidate_count"] == 1
    assert response.json()["cleanup_candidates"][0]["name"] == "old-helper"
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd apps/server && uv run pytest tests/test_skills.py -q
```

Expected: fail because the route is missing.

- [ ] **Step 3: Add route**

In `apps/server/arden/server/routers/skills.py`:

```python
@router.get("/governance")
async def skill_governance(svc: SkillService = Depends(require_skill_service)):
    return svc.governance_report()
```

This router can return the service dict directly, matching the existing `/skills` endpoints in the same file.

- [ ] **Step 4: Verify green**

Run:

```bash
cd apps/server && uv run pytest tests/test_skills.py -q
```

Expected: pass.

## Task 5: Document Metadata And Non-Mutating Cleanup

**Files:**
- Modify: `docs/guides/skills.mdx`

- [ ] **Step 1: Add governance section**

Add:

```md
## Governance

Skill metadata is validated at registry load time. `name` must match the directory name and use lowercase letters, digits, and hyphens. `description` is required.

Optional governance fields:

| Field | Meaning |
| --- | --- |
| `source` | Where the skill came from, such as `github:owner/repo/path` or `local`. |
| `version` | Version, date, commit, or release label reviewed by the user. |
| `reviewed_at` | ISO date for the last human review. |

`GET /skills/governance` returns inventory, validation issues, and cleanup candidates. Cleanup candidates are read-only recommendations; Arden does not remove skills unless the user explicitly uses the remove action.
```

- [ ] **Step 2: Verify docs diff**

Run:

```bash
git diff --check -- docs/guides/skills.mdx
```

Expected: no output.

## Task 6: Final Verification

- [ ] Run focused tests.

```bash
cd apps/server && uv run pytest tests/test_skills.py -q
```

Expected: pass.

- [ ] Run lint/diff checks.

```bash
cd apps/server && uv run ruff check arden/skills/registry.py arden/skills/service.py arden/server/routers/skills.py arden/server/schemas.py
git diff --check
```

Expected: both pass.

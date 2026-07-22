import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from arden.automation.models import Automation
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True)
class FileSchedule:
    id: str
    path: Path
    cron: str
    prompt: str
    channel: str = "chat"
    timezone: str | None = None


def discover_schedules(root: Path | str = ".") -> list[FileSchedule]:
    root = Path(root).resolve()
    base = root / "agent" / "schedules"
    if not base.exists():
        return []
    schedules = []
    for path in sorted(p for p in base.rglob("*") if p.is_file() and p.suffix in {".md", ".yaml", ".yml"}):
        parsed = _parse_schedule(path, base)
        if parsed:
            schedules.append(parsed)
    return schedules


async def compile_schedules_to_automations(
    root: Path | str,
    store: AutomationStore,
    *,
    now: datetime | None = None,
) -> list[str]:
    now = now or datetime.now(UTC)
    compiled: list[str] = []
    for schedule in discover_schedules(root):
        trigger = _cron_to_trigger(schedule.cron)
        task_id = f"fs:{schedule.id}"
        automation = Automation(
            task_id=task_id,
            name=schedule.id,
            description=schedule.prompt,
            model=None,
            triggers=[trigger],
            enabled=True,
            created_at=now,
            next_run_at=trigger.next_run(now),
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=False,
            handler=None,
        )
        await store.save(automation)
        compiled.append(task_id)
    return compiled


def _parse_schedule(path: Path, base: Path) -> FileSchedule | None:
    raw = path.read_text()
    if path.suffix == ".md":
        data, body = _parse_markdown(raw)
    else:
        data = yaml.safe_load(raw) or {}
        body = ""
    if not isinstance(data, dict):
        return None
    cron = data.get("cron")
    prompt = data.get("prompt") or body.strip()
    if not isinstance(cron, str) or not isinstance(prompt, str) or not prompt.strip():
        return None
    path_id = path.relative_to(base).with_suffix("").as_posix()
    return FileSchedule(
        id=str(data.get("id") or path_id),
        path=path,
        cron=cron,
        prompt=prompt.strip(),
        channel=str(data.get("channel") or "chat"),
        timezone=str(data["timezone"]) if data.get("timezone") else None,
    )


def _parse_markdown(raw: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    data = yaml.safe_load(match.group(1)) or {}
    return data if isinstance(data, dict) else {}, raw[match.end() :]


def _cron_to_trigger(cron: str) -> TimeTrigger:
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"Unsupported cron expression: {cron!r}")
    minute, hour, day_of_month, month, day_of_week = parts
    if day_of_month != "*" or month != "*" or not minute.isdigit() or not hour.isdigit():
        raise ValueError(f"Unsupported cron expression: {cron!r}")
    days = _cron_days(day_of_week)
    return TimeTrigger(at=f"{int(hour):02d}:{int(minute):02d}", days=days)


def _cron_days(day_of_week: str) -> str:
    if day_of_week == "*":
        return "daily"
    day_map = {"0": "sun", "1": "mon", "2": "tue", "3": "wed", "4": "thu", "5": "fri", "6": "sat"}
    try:
        return ",".join(day_map[p] for p in day_of_week.split(","))
    except KeyError:
        raise ValueError(f"Unsupported cron day field: {day_of_week!r}")

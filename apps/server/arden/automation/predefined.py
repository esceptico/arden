from datetime import UTC, datetime

from arden.automation.models import Automation
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger
from arden.constants import DAILY_NOTES_AUTOMATION_ID

DAILY_NOTES_SEED = "predefined-user-automation:daily-notes:v1"
DAILY_NOTES_AT = "23:55"
DAILY_NOTES_PROMPT = """Maintain today's daily wiki note.

1. Call current_time to get the local calendar date.
2. Read daily/README.md when it exists.
3. List sessions active within the last day and read the relevant conversations. Ignore this automation's own channel and routine automation chatter.
4. Read daily/YYYY-MM-DD.md when it exists.
5. Record only meaningful events, observations, decisions, and context that belong to that local date. Preserve useful existing notes and remove repetition.
6. Never copy Active Work, a task backlog, or general current facts merely because they remain active.
7. If there is no meaningful dated content, do not create or edit a page.
8. Otherwise create or edit only daily/YYYY-MM-DD.md with title YYYY-MM-DD.

Return a short description of what changed, or say that the day had no meaningful note."""


async def seed_predefined_user_automations(store: AutomationStore) -> bool:
    now = datetime.now(UTC)
    trigger = TimeTrigger(at=DAILY_NOTES_AT, days="daily")
    return await store.seed_predefined(
        DAILY_NOTES_SEED,
        Automation(
            task_id=DAILY_NOTES_AUTOMATION_ID,
            name="Daily Notes",
            description="Summarize meaningful events and decisions into today's daily wiki note.",
            description_source="manual",
            prompt=DAILY_NOTES_PROMPT,
            model=None,
            triggers=[trigger],
            enabled=True,
            created_at=now,
            next_run_at=trigger.next_run(now),
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=True,
            builtin=False,
            tool_scope="daily_notes",
        ),
    )

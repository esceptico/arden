import json
from dataclasses import asdict, dataclass
from pathlib import Path

from arden.workflow.models import WorkflowState


@dataclass(frozen=True)
class WorkflowStateRecord:
    session_id: str
    run_id: str
    state: WorkflowState
    reason: str | None = None


class WorkflowStateStore:
    def __init__(self, path: Path):
        self.path = path
        self._records = self._load()

    def set_state(self, session_id: str, run_id: str, state: WorkflowState, *, reason: str | None = None) -> None:
        self._records[self._key(session_id, run_id)] = WorkflowStateRecord(
            session_id=session_id,
            run_id=run_id,
            state=state,
            reason=reason,
        )
        self._save()

    def get_state(self, session_id: str, run_id: str) -> WorkflowStateRecord | None:
        return self._records.get(self._key(session_id, run_id))

    def _load(self) -> dict[str, WorkflowStateRecord]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text())
        return {
            key: WorkflowStateRecord(
                session_id=value["session_id"],
                run_id=value["run_id"],
                state=WorkflowState(value["state"]),
                reason=value.get("reason"),
            )
            for key, value in raw.items()
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: {**asdict(record), "state": record.state.value} for key, record in sorted(self._records.items())
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _key(session_id: str, run_id: str) -> str:
        return f"{session_id}:{run_id}"

import asyncio
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from arden.agent.coverage import CoverageReport, ResearchOutline, coverage_report, empty_coverage


@dataclass(frozen=True, slots=True)
class FactNote:
    claim: str
    source: str
    quote: str | None = None
    kind: Literal["fact"] = "fact"


@dataclass(frozen=True, slots=True)
class DeadEndNote:
    tried: str
    why_failed: str
    kind: Literal["dead_end"] = "dead_end"


@dataclass(frozen=True, slots=True)
class ContradictionNote:
    claim_a: str
    source_a: str
    claim_b: str
    source_b: str
    kind: Literal["contradiction"] = "contradiction"


@dataclass(frozen=True, slots=True)
class GapNote:
    what_missing: str
    kind: Literal["gap"] = "gap"


type ResearchNote = FactNote | DeadEndNote | ContradictionNote | GapNote
type ReadStatus = Literal["in_flight", "succeeded", "failed"]


@dataclass(slots=True)
class _ReadRecord:
    status: ReadStatus
    event: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(frozen=True)
class WorkItem:
    id: str
    label: str
    done: bool
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CandidateSource:
    id: str
    title: str
    locator: str
    status: Literal["candidate", "read", "rejected"] = "candidate"
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CuratedEvidence:
    claim: str
    source: str
    quote: str | None = None
    importance: Literal["low", "medium", "high", "critical"] = "medium"
    confidence: Literal["low", "medium", "high"] = "medium"
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    claim: str
    verdict: Literal["supported", "contradicted", "uncertain"]
    sources: tuple[str, ...] = ()
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceQuestion:
    question: str
    status: Literal["open", "answered", "dead_end"] = "open"
    answer_or_reason: str | None = None


@dataclass(slots=True)
class ResearchWorkspace:
    sources: dict[str, CandidateSource] = field(default_factory=dict)
    evidence: list[CuratedEvidence] = field(default_factory=list)
    verifications: list[VerificationRecord] = field(default_factory=list)
    questions: list[WorkspaceQuestion] = field(default_factory=list)
    search_history: list[str] = field(default_factory=list)

    def summary(self, *, max_items: int = 8) -> dict[str, Any]:
        evidence = sorted(
            self.evidence,
            key=lambda e: {"critical": 0, "high": 1, "medium": 2, "low": 3}[e.importance],
        )[:max_items]
        return {
            "sources": [asdict(source) for source in list(self.sources.values())[-max_items:]],
            "evidence": [asdict(item) for item in evidence],
            "verifications": [asdict(item) for item in self.verifications[-max_items:]],
            "questions": [asdict(item) for item in self.questions[-max_items:]],
            "search_history": self.search_history[-max_items:],
        }


@dataclass(slots=True)
class _CoverageState:
    outline: ResearchOutline
    section_sources: dict[str, list[str]]


class SharedLedger:
    def __init__(self) -> None:
        self._items: dict[str, WorkItem] = {}
        self._accessed: set[str] = set()
        self._notes: list[ResearchNote] = []
        self._reads: dict[str, _ReadRecord] = {}
        self._coverage: dict[str, _CoverageState] = {}
        self._workspaces: dict[str, ResearchWorkspace] = {}
        self._lock = asyncio.Lock()

    async def register(self, item_id: str, label: str, **metadata: object) -> None:
        async with self._lock:
            self._items[item_id] = WorkItem(id=item_id, label=label, done=False, metadata=dict(metadata))

    async def complete(self, item_id: str) -> None:
        async with self._lock:
            if item := self._items.get(item_id):
                self._items[item_id] = WorkItem(id=item.id, label=item.label, done=True, metadata=item.metadata)

    async def mark_accessed(self, resource_id: str) -> bool:
        async with self._lock:
            if resource_id in self._accessed:
                return True
            self._accessed.add(resource_id)
            return False

    async def claim_read(self, tool_name: str, arguments: Any) -> str | None:
        key = access_key(tool_name, arguments)
        while True:
            async with self._lock:
                self._accessed.add(key)
                record = self._reads.get(key)
                if record is None or record.status == "failed":
                    self._reads[key] = _ReadRecord("in_flight")
                    return key
                if record.status == "succeeded":
                    return None
                event = record.event
            await event.wait()

    def finish_read(self, key: str, *, succeeded: bool) -> None:
        record = self._reads.get(key)
        if record is None:
            return
        record.status = "succeeded" if succeeded else "failed"
        record.event.set()

    def add_note(self, note: ResearchNote) -> None:
        self._notes.append(note)

    @property
    def notes(self) -> list[ResearchNote]:
        return list(self._notes)

    def set_outline(self, outline: ResearchOutline, *, scope: str = "default") -> None:
        self._coverage[scope] = _CoverageState(outline=outline, section_sources=empty_coverage(outline))

    def cover_section(self, section_title: str, source: str, *, scope: str = "default") -> None:
        state = self._coverage.get(scope)
        if state is None:
            raise ValueError("no research outline is set")
        if section_title not in state.section_sources:
            raise ValueError(f"unknown outline section: {section_title}")
        if source not in state.section_sources[section_title]:
            state.section_sources[section_title].append(source)

    def coverage_report(self, *, scope: str = "default") -> CoverageReport | None:
        state = self._coverage.get(scope)
        if state is None:
            return None
        return coverage_report(state.outline, state.section_sources)

    def add_coverage_gap_notes(self, *, scope: str = "default") -> list[GapNote]:
        report = self.coverage_report(scope=scope)
        if report is None:
            return []
        added: list[GapNote] = []
        for title in report.gaps:
            note = GapNote(what_missing=f"No source covered outline section: {title}")
            if note in self._notes:
                continue
            self._notes.append(note)
            added.append(note)
        return added

    def workspace(self, *, scope: str = "default") -> ResearchWorkspace:
        workspace = self._workspaces.get(scope)
        if workspace is None:
            workspace = ResearchWorkspace()
            self._workspaces[scope] = workspace
        return workspace

    def workspace_summary(self, *, scope: str = "default", max_items: int = 8) -> dict[str, Any] | None:
        workspace = self._workspaces.get(scope)
        if workspace is None:
            return None
        summary = workspace.summary(max_items=max_items)
        if not any(summary.values()):
            return None
        return summary

    def add_workspace_source(self, source: CandidateSource, *, scope: str = "default") -> None:
        self.workspace(scope=scope).sources[source.id] = source

    def add_workspace_evidence(self, evidence: CuratedEvidence, *, scope: str = "default") -> None:
        self.workspace(scope=scope).evidence.append(evidence)

    def add_workspace_verification(self, verification: VerificationRecord, *, scope: str = "default") -> None:
        self.workspace(scope=scope).verifications.append(verification)

    def add_workspace_question(self, question: WorkspaceQuestion, *, scope: str = "default") -> None:
        self.workspace(scope=scope).questions.append(question)

    def add_workspace_search(self, query: str, *, scope: str = "default") -> None:
        query = query.strip()
        if query:
            self.workspace(scope=scope).search_history.append(query)

    def get_items(self, *, exclude_id: str | None = None) -> list[WorkItem]:
        return [item for item in self._items.values() if item.id != exclude_id]

    @property
    def accessed_count(self) -> int:
        return len(self._accessed)


def access_key(tool_name: str, arguments: Any) -> str:
    return f"{tool_name}:{format_arguments(arguments)}"


def format_arguments(arguments: Any) -> str:
    return json.dumps(normalize(arguments), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def normalize(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [normalize(item) for item in value]
    return value

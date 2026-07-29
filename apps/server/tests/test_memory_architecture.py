import ast
from pathlib import Path

ARDEN = Path(__file__).parents[1] / "arden"
CLEAN_PACKAGES = (
    ARDEN / "revisions",
    ARDEN / "memory" / "facts",
    ARDEN / "wiki",
    ARDEN / "tools" / "core",
    ARDEN / "events",
    ARDEN / "workflow",
)
CLEAN_FILES = (
    ARDEN / "server" / "stream.py",
    ARDEN / "services" / "chat_context.py",
)
FORBIDDEN_CALLS = {"getattr", "setattr", "hasattr"}


def imported_modules(source: Path) -> tuple[str, ...]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    direct = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    from_imports = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    return (*direct, *from_imports)


def test_production_imports_are_absolute_and_annotations_are_eager() -> None:
    relative_imports: list[str] = []
    future_imports: list[str] = []
    type_checking_imports: list[str] = []
    for source in ARDEN.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                relative_imports.append(f"{source}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                future_imports.append(f"{source}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and node.module == "typing":
                if any(alias.name == "TYPE_CHECKING" for alias in node.names):
                    type_checking_imports.append(f"{source}:{node.lineno}")

    assert relative_imports == []
    assert future_imports == []
    assert type_checking_imports == []


def test_refactored_packages_do_not_use_dynamic_attribute_access() -> None:
    violations: list[str] = []
    sources = [source for package in CLEAN_PACKAGES for source in package.rglob("*.py")]
    sources.extend(CLEAN_FILES)
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                violations.append(f"{source}:{node.lineno}:{node.func.id}")
            if isinstance(node, ast.Attribute) and node.attr in {"__setattr__", "__dataclass_fields__"}:
                violations.append(f"{source}:{node.lineno}:{node.attr}")

    assert violations == []


def test_revision_repository_facade_keeps_query_and_maintenance_ownership_split() -> None:
    facade = ARDEN / "revisions" / "repository.py"
    extracted = (ARDEN / "revisions" / "query.py", ARDEN / "revisions" / "maintenance.py")
    forbidden_prefixes = ("arden.server", "arden.services", "arden.tools", "arden.wiki")

    assert len(facade.read_text(encoding="utf-8").splitlines()) < 1_000
    for source in extracted:
        assert not any(module.startswith(forbidden_prefixes) for module in imported_modules(source)), source

    from arden.revisions import ManagedFileRepository
    from arden.revisions.errors import RevisionConflictError

    assert ManagedFileRepository.__name__ == "ManagedFileRepository"
    assert RevisionConflictError.__name__ == "RevisionConflictError"


def test_fact_ledger_facade_keeps_codec_and_state_ownership_split() -> None:
    facade = ARDEN / "memory" / "facts" / "ledger.py"
    extracted = (ARDEN / "memory" / "facts" / "event_codec.py", ARDEN / "memory" / "facts" / "state.py")
    forbidden_prefixes = ("arden.server", "arden.services", "arden.tools", "arden.wiki")

    assert len(facade.read_text(encoding="utf-8").splitlines()) < 1_000
    for source in extracted:
        assert not any(module.startswith(forbidden_prefixes) for module in imported_modules(source)), source

    from arden.memory.facts.event_codec import decode_event, parse_event_file
    from arden.memory.facts.ledger import FactLedger
    from arden.memory.facts.state import state_from

    assert FactLedger.__name__ == "FactLedger"
    assert decode_event.__name__ == "decode_event"
    assert parse_event_file.__name__ == "parse_event_file"
    assert state_from.__name__ == "state_from"


def test_wiki_service_facade_keeps_domain_ownership_split() -> None:
    facade = ARDEN / "wiki" / "service.py"
    extracted = (
        ARDEN / "wiki" / "snapshots.py",
        ARDEN / "wiki" / "changes.py",
        ARDEN / "wiki" / "rename.py",
        ARDEN / "wiki" / "exceptions.py",
    )
    forbidden_prefixes = (
        "arden.automation",
        "arden.memory",
        "arden.server",
        "arden.services",
        "arden.tools",
    )

    assert len(facade.read_text(encoding="utf-8").splitlines()) < 1_000
    for source in extracted:
        assert not any(module.startswith(forbidden_prefixes) for module in imported_modules(source)), source

    from arden.wiki.service import (
        GeneratedRegionConflictError,
        WikiAmbiguityError,
        WikiMaintenanceEvidenceLimitError,
        WikiService,
        WikiSnapshotChangedError,
        WikiValidationError,
    )

    assert WikiService.__name__ == "WikiService"
    assert WikiValidationError.__name__ == "WikiValidationError"
    assert WikiAmbiguityError.__name__ == "WikiAmbiguityError"
    assert WikiSnapshotChangedError.__name__ == "WikiSnapshotChangedError"
    assert GeneratedRegionConflictError.__name__ == "GeneratedRegionConflictError"
    assert WikiMaintenanceEvidenceLimitError.__name__ == "WikiMaintenanceEvidenceLimitError"


def test_scheduler_facade_keeps_dispatch_and_execution_ownership_split() -> None:
    facade = ARDEN / "automation" / "scheduler.py"
    extracted = (
        ARDEN / "automation" / "event_dispatch.py",
        ARDEN / "automation" / "run_execution.py",
    )
    forbidden_prefixes = (
        "arden.server",
        "arden.services",
        "arden.tools",
        "arden.wiki",
    )

    assert len(facade.read_text(encoding="utf-8").splitlines()) < 1_000
    for source in extracted:
        assert not any(module.startswith(forbidden_prefixes) for module in imported_modules(source)), source

    from arden.automation.scheduler import (
        AUTOMATION_BUS_KEY,
        CompletedAgentRun,
        DetachedRun,
        DetachedRunBindingPending,
        RunDeferred,
        RunSkipped,
        Scheduler,
        split_manual_flag,
    )

    assert AUTOMATION_BUS_KEY == "automation:events"
    assert Scheduler.__name__ == "Scheduler"
    assert CompletedAgentRun.__name__ == "CompletedAgentRun"
    assert DetachedRun.__name__ == "DetachedRun"
    assert DetachedRunBindingPending.__name__ == "DetachedRunBindingPending"
    assert RunDeferred.__name__ == "RunDeferred"
    assert RunSkipped.__name__ == "RunSkipped"
    assert split_manual_flag.__name__ == "split_manual_flag"

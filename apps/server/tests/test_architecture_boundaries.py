import ast
import re
from pathlib import Path

from arden.server.app import app

SERVER_DIR = Path(__file__).resolve().parents[1]
SERVER_ROOT = SERVER_DIR / "arden"


def server_path(path: str) -> Path:
    return Path(path)


def _sql_strings(source: str) -> list[str]:
    strings: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            strings.append("".join(part.value for part in node.values if isinstance(part, ast.Constant)))
    return strings


def _references_sql_table(source: str, table: str) -> bool:
    name = re.escape(table)
    pattern = re.compile(
        rf"(?:"
        rf"\b(?:from|join|into|update|delete\s+from|alter\s+table|drop\s+table)\s+[`\"\[]?{name}\b"
        rf"|\bcreate\s+table(?:\s+if\s+not\s+exists)?\s+[`\"\[]?{name}\b"
        rf"|\bcreate(?:\s+unique)?\s+index\b.*?\bon\s+[`\"\[]?{name}\b"
        rf"|\bpragma\s+table_info\s*\(\s*[`\"\[]?{name}\b"
        rf")",
        re.IGNORECASE | re.DOTALL,
    )
    return any(pattern.search(value) for value in _sql_strings(source))


def test_backend_persistence_tables_are_only_referenced_by_owner_modules():
    automation_persistence = {
        server_path("arden/automation/store.py"),
        server_path("arden/automation/store_queries.py"),
        server_path("arden/automation/store_schema.py"),
    }
    table_owners = {
        "outbox_events": {server_path("arden/outbox/store.py")},
        "scheduled_tasks": automation_persistence,
        "automation_runs": automation_persistence,
        "automation_event_dedupe": automation_persistence,
        "automation_event_queue": automation_persistence,
        "automation_event_dead_letter": automation_persistence,
        "automation_count_state": automation_persistence,
        "automation_meta": automation_persistence,
        "automation_idempotency_claims": automation_persistence,
        "monitor_state": {server_path("arden/monitor/store.py")},
    }

    violations: list[str] = []
    for path in SERVER_ROOT.rglob("*.py"):
        rel = path.relative_to(SERVER_DIR)
        source = path.read_text()
        for table, allowed_paths in table_owners.items():
            if _references_sql_table(source, table) and rel not in allowed_paths:
                violations.append(f"{table} referenced from {rel}")

    assert violations == []


def test_persistence_boundary_ignores_semantic_names_but_finds_sql():
    assert not _references_sql_table('automation_runs = {}\ndef list_automation_runs(): ...', "automation_runs")
    assert _references_sql_table('QUERY = "SELECT * FROM automation_runs"', "automation_runs")
    assert _references_sql_table('QUERY = "PRAGMA table_info(automation_runs)"', "automation_runs")


def test_services_do_not_import_runtime_composition_root():
    violations: list[str] = []
    for path in SERVER_ROOT.joinpath("services").rglob("*.py"):
        rel = path.relative_to(SERVER_DIR)
        source = path.read_text()
        if "arden.server.runtime" in source:
            violations.append(f"runtime imported from {rel}")

    assert violations == []


def test_tool_context_does_not_own_background_task_registry():
    context_source = SERVER_ROOT.joinpath("tools/core/context.py").read_text()
    registry_source = SERVER_ROOT.joinpath("tools/core/background_tasks.py").read_text()

    assert "class BackgroundTaskRegistry" not in context_source
    assert "class BackgroundTaskRegistry" in registry_source


def test_server_app_does_not_own_http_route_handlers():
    source = SERVER_ROOT.joinpath("server/app.py").read_text()
    route_decorators = ("@app.get(", "@app.post(", "@app.put(", "@app.patch(", "@app.delete(")

    assert not any(decorator in source for decorator in route_decorators)


def test_server_routes_are_unique_by_method_and_path():
    seen: dict[tuple[tuple[str, ...], str], str] = {}
    duplicates: list[str] = []

    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        key = (tuple(sorted(methods)), path)
        name = getattr(route, "name", "<unnamed>")
        if key in seen:
            duplicates.append(f"{','.join(key[0])} {path}: {seen[key]} and {name}")
        else:
            seen[key] = name

    assert duplicates == []

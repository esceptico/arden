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

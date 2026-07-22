from pathlib import Path

from arden.agent_surface.models import AgentSurfaceManifest, SurfaceCapability, SurfaceWarning, relative_posix


def _path_id(path: Path, base: Path, suffix: str | None = None) -> str:
    rel = path.relative_to(base)
    if suffix and rel.name == suffix:
        rel = rel.parent
    else:
        rel = rel.with_suffix("")
    return rel.as_posix()


def _model_safe_name(path_id: str) -> str:
    return path_id.replace("/", "_").replace("-", "_")


def discover_agent_surface(root: Path | str = ".") -> AgentSurfaceManifest:
    root = Path(root).resolve()
    agent_root = root / "agent"
    manifest = AgentSurfaceManifest()
    if not agent_root.exists():
        return manifest
    if not agent_root.is_dir():
        manifest.warnings.append(
            SurfaceWarning(
                path=relative_posix(agent_root, root), code="not_directory", message="agent path is not a directory"
            )
        )
        return manifest

    _discover_skills(root, agent_root, manifest)
    _discover_schedules(root, agent_root, manifest)
    _discover_simple_tree(root, agent_root, manifest, "tools", manifest.tools)
    _discover_simple_tree(root, agent_root, manifest, "hooks", manifest.hooks)
    _discover_simple_tree(root, agent_root, manifest, "channels", manifest.channels)
    _discover_simple_tree(root, agent_root, manifest, "subagents", manifest.subagents)
    return manifest


def _discover_skills(root: Path, agent_root: Path, manifest: AgentSurfaceManifest) -> None:
    base = agent_root / "skills"
    if not base.exists():
        return
    for skill_md in sorted(base.glob("*/SKILL.md")):
        path_id = _path_id(skill_md, base, "SKILL.md")
        manifest.skills.append(
            SurfaceCapability(id=path_id, model_name=_model_safe_name(path_id), path=relative_posix(skill_md, root))
        )


def _discover_schedules(root: Path, agent_root: Path, manifest: AgentSurfaceManifest) -> None:
    base = agent_root / "schedules"
    if not base.exists():
        return
    for path in sorted(p for p in base.rglob("*") if p.is_file() and p.suffix in {".md", ".yaml", ".yml"}):
        path_id = _path_id(path, base)
        manifest.schedules.append(
            SurfaceCapability(id=path_id, model_name=_model_safe_name(path_id), path=relative_posix(path, root))
        )


def _discover_simple_tree(
    root: Path,
    agent_root: Path,
    manifest: AgentSurfaceManifest,
    name: str,
    target: list[SurfaceCapability],
) -> None:
    base = agent_root / name
    if not base.exists():
        return
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        path_id = _path_id(path, base)
        target.append(
            SurfaceCapability(id=path_id, model_name=_model_safe_name(path_id), path=relative_posix(path, root))
        )

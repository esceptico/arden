from pathlib import Path

from pydantic import BaseModel, Field


class SurfaceCapability(BaseModel):
    id: str
    model_name: str | None = None
    path: str
    source: str = "filesystem"


class SurfaceWarning(BaseModel):
    path: str
    code: str
    message: str


class AgentSurfaceInfo(BaseModel):
    root: str = "agent/"
    manifest_path: str = ".arden/manifest.json"


class AgentSurfaceManifest(BaseModel):
    version: str = "1"
    agent_surface: AgentSurfaceInfo = Field(default_factory=AgentSurfaceInfo)
    tools: list[dict] = Field(default_factory=list)
    deferred_tool_groups: list[dict] = Field(default_factory=list)
    skills: list[SurfaceCapability] = Field(default_factory=list)
    automations: list[SurfaceCapability] = Field(default_factory=list)
    schedules: list[SurfaceCapability] = Field(default_factory=list)
    channels: list[SurfaceCapability] = Field(default_factory=list)
    hooks: list[SurfaceCapability] = Field(default_factory=list)
    subagents: list[SurfaceCapability] = Field(default_factory=list)
    sandbox: dict = Field(default_factory=dict)
    event_types: list[str] = Field(default_factory=list)
    warnings: list[SurfaceWarning] = Field(default_factory=list)


class RuntimeInfo(AgentSurfaceManifest):
    version: str


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()

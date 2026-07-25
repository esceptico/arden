from pydantic import BaseModel, Field

from arden.logging import get_logger
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

_logger = get_logger(__name__)


class UseSkillInput(BaseModel):
    skill: str = Field(description="Name of the skill to activate")
    args: str = Field(default="", description="Optional arguments for the skill")


USE_SKILL_DESCRIPTION = (
    "Activate a skill to get specialized instructions for a task. "
    "Available skills are listed in the system prompt under <available_skills>. "
    "Use this tool with the skill name and optional arguments. "
    "When a skill matches the user's request, invoke it BEFORE generating any other response about the task."
)


async def use_skill(execution: ToolExecution, args: UseSkillInput) -> ToolResult:
    registry = execution.ctx.services["skill_registry"]
    meta = registry.get(args.skill)
    content = registry.render_skill_xml(args.skill, args.args)
    if meta is None or content is None:
        available = ", ".join(registry.names)
        return ToolResult(
            content=f"Unknown skill: {args.skill}. Available: {available}",
            preview=f"Unknown skill: {args.skill}",
            is_error=True,
        )

    return ToolResult(content=content, preview=f"Loaded skill: {args.skill}")


use_skill_tool = tool(
    display_name="UseSkill",
    display_description="Load instructions for a specialized skill.",
    description=USE_SKILL_DESCRIPTION,
    input_model=UseSkillInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"skill_registry"}),
    ),
    execute=use_skill,
)


# --- Create skill ---

CREATE_SKILL_DESCRIPTION = (
    "Create a new global skill at ~/.arden/skills/<name>/SKILL.md from inline "
    "content. The skill becomes immediately available via /<name> in chat "
    "and shows up in the slash picker. Use after the propose-skill flow "
    "when the user wants to capture this conversation as a reusable "
    "procedure. Requires approval — the user sees the full body before save."
)


class CreateSkillInput(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=48,
        description="Lowercase hyphenated name, e.g. 'refactor-component'. Must start with a letter; letters/digits/hyphens only.",
    )
    description: str = Field(
        min_length=1,
        max_length=1024,
        description="One-line description: what the skill does AND when to use it. This is what the agent reads to decide whether to activate the skill.",
    )
    body: str = Field(
        min_length=1,
        max_length=100_000,
        description="The SKILL.md body, after the frontmatter (the system adds frontmatter from name + description). Markdown. Start with a # heading.",
    )


async def approve_create_skill(execution: ToolExecution, args: CreateSkillInput) -> ApprovalInfo | None:
    # The approval card surfaces the name, description, and a body excerpt
    # so the user can decide without opening anything else.
    preview = f"Name: {args.name}\nDescription: {args.description}\nBody:\n{args.body}"
    return ApprovalInfo(
        description=f"Create skill: {args.name}",
        preview=preview,
        diff=None,
    )


async def create_skill(execution: ToolExecution, args: CreateSkillInput) -> ToolResult:
    svc = execution.ctx.services.get("skill_service")
    if svc is None:
        return ToolResult(content="Skill service unavailable.", preview="Unavailable", is_error=True)
    try:
        meta = svc.create(args.name, args.description, args.body)
    except ValueError as e:
        return ToolResult(content=f"Error: {e}", preview="Failed", is_error=True)
    except FileExistsError as e:
        return ToolResult(content=f"Conflict: {e}", preview="Already exists", is_error=True)

    return ToolResult(
        content=f"Created skill '{meta.name}' at {meta.path}/SKILL.md. Available as /{meta.name}.",
        preview=f"Created /{meta.name}",
    )


create_skill_tool = tool(
    display_name="CreateSkill",
    display_description="Create a reusable global skill.",
    description=CREATE_SKILL_DESCRIPTION,
    input_model=CreateSkillInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"skill_service"}),
    ),
    approval=approve_create_skill,
    execute=create_skill,
)

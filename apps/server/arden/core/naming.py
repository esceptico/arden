from pydantic import BaseModel, ConfigDict, Field

from arden.agent import CompletionResponse, Role
from arden.core.llm_client import llm_client
from arden.logging import get_logger
from arden.observability import observed_trace

_logger = get_logger(__name__)

SESSION_NAMING_PROMPT = """Generate a concise session name from the user's first message.

Rules:
- 3-7 words.
- Sentence case: capitalize only the first word and proper nouns/acronyms.
- Name the actual topic or goal, not the request wrapper.
- Keep enough detail to recognize the session later.
- Return a JSON object matching the schema.

Good:
{"name": "Fix checkout retry bug"}
{"name": "Research agent session naming"}
{"name": "Debug SSE approval replay"}

Bad:
{"name": "Code changes"}
{"name": "Research task"}
{"name": "Please Review And Improve The Prompt For Naming Research Agents"}
"""

AGENT_NAMING_PROMPT = """Generate a short display label for a spawned agent.

Rules:
- 2-5 words.
- Sentence case: capitalize only the first word and proper nouns/acronyms.
- Name the task topic only. The UI already shows this row is an agent.
- Do not prefix the name with Research, Agent, Subagent, or any role label.
- Do not copy the full task text.
- Return a JSON object matching the schema.

Good:
{"name": "Eval test harness"}
{"name": "Session naming prompts"}
{"name": "Update docs"}

Bad:
{"name": "Agent"}
{"name": "Research eval test harness"}
{"name": "Agent: eval test harness"}
{"name": "Inspect current eval/test harness opportunities"}
{"name": "Eval Test Harness"}
"""


class NameOutput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)


def _response_name(response: CompletionResponse) -> str:
    content = response.choices[0].message.content if response.choices else None
    return NameOutput.model_validate_json(content or "{}").name


async def _generate_name(
    *,
    model: str,
    system_prompt: str,
    user_content: str,
    fallback: str,
    log_subject: str,
) -> str:
    try:
        response = await llm_client.complete(
            model=model,
            messages=[
                {"role": Role.SYSTEM, "content": system_prompt},
                {"role": Role.USER, "content": user_content},
            ],
            temperature=0,
            max_tokens=80,
            response_format=NameOutput,
        )
        return _response_name(response)
    except Exception as exc:
        _logger.warning("%s name generation failed: %s", log_subject, exc)
        return fallback


@observed_trace("session.name", tags="session")
async def generate_conversation_name(model: str, text: str, *, has_images: bool = False) -> str:
    fallback = "New Conversation"
    if not text.strip() and not has_images:
        return fallback
    first_message = text if text.strip() else "[no text]"
    image_note = "\nThe user also attached images." if has_images else ""
    return await _generate_name(
        model=model,
        system_prompt=SESSION_NAMING_PROMPT,
        user_content=f"First user message:\n{first_message}{image_note}",
        fallback=fallback,
        log_subject="Session",
    )


@observed_trace("agent.name", tags="agent")
async def generate_agent_name(model: str, task: str) -> str:
    if not task.strip():
        return "Agent"
    return await _generate_name(
        model=model,
        system_prompt=AGENT_NAMING_PROMPT,
        user_content=f"Task:\n{task}",
        fallback="Agent",
        log_subject="Agent",
    )

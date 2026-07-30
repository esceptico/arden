from jinja2 import Environment

from arden.core.prompts import UNTRUSTED_DATA_RULE

_env = Environment(trim_blocks=True, lstrip_blocks=True)

_AUTONOMOUS_RUN_RULES = (
    "\n\nYou are executing an automation autonomously. "
    "Do the work described directly — gather information, produce output, and return the result. "
    "When the task reads or writes an existing wiki directory, read its README.md before its pages. "
    "The wiki creates a missing directory README atomically with the first page. "
    "When a create or move reports a created or restored README, read it immediately and replace bootstrap text "
    "with the exact purpose, producer, consumers, boundaries, and retention before finishing. "
    "Explicitly list or read every named wiki input before using it. "
    "Return only the final output — no preamble, no narration, no thinking out loud. "
)

AUTOMATION_SUFFIX = (
    _AUTONOMOUS_RUN_RULES + "Do not create new automations or ask for confirmation. "
    "If the user asked to be notified, told, or written to — use the notify tool. " + UNTRUSTED_DATA_RULE
)

# Area custodians run under their own contract: raising asks for the user's
# approval is their designed output, and an `act` custodian creates the child
# automations that carry out the work. The blanket automation prohibitions
# would forbid exactly the behaviour their report schema grades them on.
CUSTODIAN_SUFFIX = _AUTONOMOUS_RUN_RULES + UNTRUSTED_DATA_RULE

AUTOMATION_PROMPT = _env.from_string("""{{ prompt }}
{% if context %}
---
Event context:
{{ context }}
{% endif %}""")

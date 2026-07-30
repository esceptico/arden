from arden.server.schemas import CreateAutomationRequest
from arden.tools.automation import CreateAutomationInput


def test_create_automation_prompt_is_required_by_both_input_schemas():
    assert CreateAutomationRequest.model_fields["prompt"].is_required()
    assert CreateAutomationInput.model_fields["prompt"].is_required()

from arden.agent import CompletionResponse, Usage
from arden.llm.models import get_model


class UsageTracker:
    def __init__(self) -> None:
        self.usage = Usage()
        self.cost: float = 0.0

    async def track(self, response: CompletionResponse) -> None:
        self.usage += response.usage
        # Unknown / unregistered model → treat as $0 cost rather than crashing
        # the agent loop. Hit by tests using stub model names and by users
        # adding custom models that haven't yet declared pricing.
        try:
            self.cost += get_model(response.model).pricing.cost(response.usage)
        except ValueError:
            pass

from arden.agent import CompletionResponse, Usage
from arden.llm.models import get_model


class UsageTracker:
    def __init__(self) -> None:
        self.usage = Usage()
        self.cost: float = 0.0
        # Direct responses only. `cost` additionally includes child-agent
        # rollups so budget enforcement sees the whole run.
        self.own_cost: float = 0.0

    async def track(self, response: CompletionResponse) -> None:
        response_cost = get_model(response.model).pricing.cost(response.usage)
        self.usage += response.usage
        self.cost += response_cost
        self.own_cost += response_cost

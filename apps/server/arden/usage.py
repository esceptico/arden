from dataclasses import dataclass

from arden.agent import Usage


@dataclass(frozen=True)
class Pricing:
    price_in: float
    price_out: float
    price_cache_read: float = 0
    price_cache_write: float = 0

    def cost(self, usage: Usage) -> float:
        return (
            usage.prompt_tokens * self.price_in
            + usage.completion_tokens * self.price_out
            + usage.cache_read_tokens * self.price_cache_read
            + usage.cache_write_tokens * self.price_cache_write
        ) / 1_000_000

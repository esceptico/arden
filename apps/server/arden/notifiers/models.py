import json
from dataclasses import dataclass
from datetime import datetime


def _to_dt(v):
    return datetime.fromisoformat(v) if isinstance(v, str) else v


@dataclass
class NotifierConfig:
    name: str
    type: str
    config: dict
    created_at: datetime

    def __post_init__(self):
        if isinstance(self.config, str):
            self.config = json.loads(self.config)
        self.created_at = _to_dt(self.created_at)

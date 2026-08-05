from pydantic import BaseModel, ConfigDict


class SlackToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

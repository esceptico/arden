from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


class _SlackModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlackPlainText(_SlackModel):
    type: Literal["plain_text"]
    text: str = Field(min_length=1, max_length=3_000)
    emoji: bool | None = None


class SlackMrkdwnText(_SlackModel):
    type: Literal["mrkdwn"]
    text: str = Field(min_length=1, max_length=3_000)
    verbatim: bool | None = None


SlackText = Annotated[SlackPlainText | SlackMrkdwnText, Field(discriminator="type")]


class SlackHeaderText(SlackPlainText):
    text: str = Field(min_length=1, max_length=150)


class SlackButtonText(SlackPlainText):
    text: str = Field(min_length=1, max_length=75)


class SlackSectionFieldPlainText(SlackPlainText):
    text: str = Field(min_length=1, max_length=2_000)


class SlackSectionFieldMrkdwnText(SlackMrkdwnText):
    text: str = Field(min_length=1, max_length=2_000)


SlackSectionFieldText = Annotated[
    SlackSectionFieldPlainText | SlackSectionFieldMrkdwnText,
    Field(discriminator="type"),
]


class SlackImageTitleText(SlackPlainText):
    text: str = Field(min_length=1, max_length=2_000)


class SlackButton(_SlackModel):
    type: Literal["button"]
    text: SlackButtonText
    action_id: str = Field(min_length=1, max_length=255)
    value: str | None = Field(default=None, max_length=2_000)
    url: str | None = Field(default=None, min_length=1, max_length=3_000)
    style: Literal["primary", "danger"] | None = None
    accessibility_label: str | None = Field(default=None, max_length=75)


class SlackImageElement(_SlackModel):
    type: Literal["image"]
    image_url: str = Field(min_length=1, max_length=3_000)
    alt_text: str = Field(min_length=1, max_length=2_000)


SlackSectionAccessory = Annotated[SlackButton | SlackImageElement, Field(discriminator="type")]
SlackContextElement = Annotated[SlackPlainText | SlackMrkdwnText | SlackImageElement, Field(discriminator="type")]


class SlackHeaderBlock(_SlackModel):
    type: Literal["header"]
    text: SlackHeaderText
    block_id: str | None = Field(default=None, max_length=255)


class SlackSectionBlock(_SlackModel):
    type: Literal["section"]
    text: SlackText | None = None
    fields: list[SlackSectionFieldText] | None = Field(default=None, min_length=1, max_length=10)
    accessory: SlackSectionAccessory | None = None
    block_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _require_content(self) -> "SlackSectionBlock":
        if self.text is None and self.fields is None:
            raise ValueError("section requires text or fields")
        return self


class SlackContextBlock(_SlackModel):
    type: Literal["context"]
    elements: list[SlackContextElement] = Field(min_length=1, max_length=10)
    block_id: str | None = Field(default=None, max_length=255)


class SlackDividerBlock(_SlackModel):
    type: Literal["divider"]
    block_id: str | None = Field(default=None, max_length=255)


class SlackImageBlock(_SlackModel):
    type: Literal["image"]
    image_url: str = Field(min_length=1, max_length=3_000)
    alt_text: str = Field(min_length=1, max_length=2_000)
    title: SlackImageTitleText | None = None
    block_id: str | None = Field(default=None, max_length=255)


class SlackActionsBlock(_SlackModel):
    type: Literal["actions"]
    elements: list[SlackButton] = Field(min_length=1, max_length=25)
    block_id: str | None = Field(default=None, max_length=255)


SlackBlock = Annotated[
    SlackHeaderBlock | SlackSectionBlock | SlackContextBlock | SlackDividerBlock | SlackImageBlock | SlackActionsBlock,
    Field(discriminator="type"),
]


class SlackBlockEnvelope(RootModel[list[SlackBlock]]):
    root: list[SlackBlock] = Field(min_length=1, max_length=50)

    def provider_payload(self) -> list[dict[str, object]]:
        return [block.model_dump(mode="json", exclude_none=True) for block in self.root]

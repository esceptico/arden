from abc import ABC, abstractmethod

from arden.channels.models import ChannelDeliveryHandle, ChannelEnvelope, RuntimeIdentity


class ChannelAdapter(ABC):
    channel: str

    @abstractmethod
    async def normalize_inbound(self, payload: dict) -> ChannelEnvelope: ...

    @abstractmethod
    async def render_output(self, runtime: RuntimeIdentity, text: str) -> dict: ...

    @abstractmethod
    async def render_approval(self, delivery: ChannelDeliveryHandle, approval: dict) -> dict: ...

    @abstractmethod
    async def render_auth_required(self, delivery: ChannelDeliveryHandle, auth: dict) -> dict: ...

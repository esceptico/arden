from arden.channels.base import ChannelAdapter
from arden.channels.models import ChannelDeliveryHandle, ChannelEnvelope, RuntimeIdentity


class EmailChannelAdapter(ChannelAdapter):
    channel = "email"

    async def normalize_inbound(self, payload: dict) -> ChannelEnvelope:
        thread_id = str(payload.get("thread_id") or payload.get("message_id") or "")
        delivery = ChannelDeliveryHandle(
            channel=self.channel,
            native_thread_id=thread_id,
            native_message_id=payload.get("message_id"),
            continuation_token=payload.get("continuation_token"),
        )
        runtime = RuntimeIdentity(session_id=str(payload.get("session_id") or ""))
        return ChannelEnvelope(
            delivery=delivery, runtime=runtime, text=str(payload.get("text") or ""), metadata=payload
        )

    async def render_output(self, runtime: RuntimeIdentity, text: str) -> dict:
        return {"channel": self.channel, "session_id": runtime.session_id, "text": text}

    async def render_approval(self, delivery: ChannelDeliveryHandle, approval: dict) -> dict:
        return {"channel": self.channel, "thread": delivery.native_thread_id, "approval": approval}

    async def render_auth_required(self, delivery: ChannelDeliveryHandle, auth: dict) -> dict:
        return {"channel": self.channel, "thread": delivery.native_thread_id, "auth": auth}

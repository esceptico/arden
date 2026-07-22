from arden.channels.base import ChannelAdapter
from arden.channels.models import ChannelDeliveryHandle, ChannelEnvelope, RuntimeIdentity


class SlackChannelAdapter(ChannelAdapter):
    channel = "slack"

    async def normalize_inbound(self, payload: dict) -> ChannelEnvelope:
        channel_id = str(payload.get("channel_id") or payload.get("channel") or "")
        ts = str(payload.get("thread_ts") or payload.get("ts") or "")
        delivery = ChannelDeliveryHandle(
            channel=self.channel,
            native_thread_id=f"{channel_id}:{ts}",
            native_message_id=str(payload.get("ts") or ""),
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

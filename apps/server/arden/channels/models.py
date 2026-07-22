from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelDeliveryHandle:
    channel: str
    native_thread_id: str
    native_message_id: str | None = None
    continuation_token: str | None = None


@dataclass(frozen=True)
class RuntimeIdentity:
    session_id: str
    run_id: str | None = None
    turn_id: str | None = None
    step_id: str | None = None
    cursor: str | None = None


@dataclass(frozen=True)
class ChannelEnvelope:
    delivery: ChannelDeliveryHandle
    runtime: RuntimeIdentity
    text: str
    metadata: dict | None = None

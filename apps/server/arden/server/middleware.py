from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from arden.observability.judgment import activate_tracing
from arden.server.runtime import Runtime
from arden.settings import verify_api_key


class TracingMiddleware:
    """Pure ASGI middleware — re-asserts judgeval's active tracer per request.

    judgeval's active tracer lives in a ContextVar set during the lifespan task,
    which request tasks don't inherit; without this, @Tracer.observe spans no-op in
    request handlers (and tasks they spawn, e.g. run_chat). Pure ASGI so it doesn't
    buffer streaming responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            activate_tracing()
        await self.app(scope, receive, send)


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    return ""


class AuthMiddleware:
    """Pure ASGI middleware — doesn't buffer streaming responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        runtime: Runtime | None = getattr(request.app.state, "runtime", None)
        if not runtime:
            await self.app(scope, receive, send)
            return

        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        public_paths = {"/health"}
        # These endpoints authenticate with the enrolled executor-device token
        # via their own dependency; enrollment and device management still
        # require the API key.
        executor_device_paths = {
            "/executor/stream",
            "/executor/started",
            "/executor/results",
            "/executor/heartbeat",
            "/executor/skills",
            "/executor/skills/bodies",
        }
        path = request.url.path
        if path not in public_paths and path not in executor_device_paths:
            token = _extract_bearer_token(request)
            if not token:
                detail = "Missing API key. Include Authorization: Bearer <key> header."
            elif not runtime.config.api_key_hash:
                detail = "No API key configured. Restart server to generate one."
            elif not verify_api_key(token, runtime.config.api_key_hash):
                detail = "Invalid API key. Run 'arden-server serve --reset-key' to generate a new one."
            else:
                detail = None
            if detail:
                response = JSONResponse(status_code=401, content={"detail": detail})
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


class SSEStreamingResponse(StreamingResponse):
    """StreamingResponse that skips listen_for_disconnect task group.

    Starlette's default __call__ spawns a parallel listen_for_disconnect
    task when ASGI spec < 2.4 (uvicorn HTTP reports 2.3). On shutdown,
    cancelling that task produces noisy CancelledError tracebacks and
    blocks uvicorn's graceful exit. We skip it — our generator handles
    its own lifecycle via CancelledError.
    """

    async def __call__(self, scope, receive, send):
        try:
            await self.stream_response(send)
        except OSError:
            pass
        finally:
            if self.background is not None:
                await self.background()

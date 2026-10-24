"""Bound request bytes and in-flight work before FastAPI parses JSON."""

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class HttpLimits:
    def __init__(
        self, app: ASGIApp, max_body_bytes: int, max_inflight: int, body_timeout: float
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_inflight = max_inflight
        self.body_timeout = body_timeout
        self.active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        health = scope.get("path") in ("/health/live", "/health/ready", "/health/worker")
        if not health and self.active >= self.max_inflight:
            await JSONResponse(
                {"detail": "Service is busy; retry with the same ticket ID"},
                status_code=503,
                headers={"Retry-After": "1", "Connection": "close"},
            )(scope, receive, send)
            return
        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    length = -1
                if length < 0:
                    await JSONResponse(
                        {"detail": "Invalid Content-Length"}, 400, headers={"Connection": "close"}
                    )(scope, receive, send)
                    return
                if length > self.max_body_bytes:
                    await self.too_large(scope, receive, send)
                    return
        if not health:
            self.active += 1
        try:
            body = bytearray()
            try:
                async with asyncio.timeout(self.body_timeout):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > self.max_body_bytes:
                            await self.too_large(scope, receive, send)
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await JSONResponse(
                    {"detail": "Request body timed out"}, 408, headers={"Connection": "close"}
                )(scope, receive, send)
                return
            delivered = False

            async def replay() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay, send)
        finally:
            if not health:
                self.active -= 1

    async def too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse({"detail": "Request body exceeds the byte limit"}, 413)(
            scope, receive, send
        )

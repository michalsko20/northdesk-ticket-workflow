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
                # Leave connection lifecycle to the server: closing while the
                # caller is uploading can turn this rejection into a TCP reset.
                headers={"Retry-After": "1"},
            )(scope, receive, send)
            return
        content_lengths = [
            value for key, value in scope.get("headers", []) if key.lower() == b"content-length"
        ]
        transfer_encodings = [
            value for key, value in scope.get("headers", []) if key.lower() == b"transfer-encoding"
        ]
        # Request smuggling defense: reject conflicting Content-Length values
        # and combined Content-Length + Transfer-Encoding.
        if len(content_lengths) > 1 and len(set(content_lengths)) > 1:
            await JSONResponse(
                {"detail": "Conflicting Content-Length headers"},
                400,
                headers={"Connection": "close"},
            )(scope, receive, send)
            return
        if transfer_encodings and content_lengths:
            await JSONResponse(
                {"detail": "Both Content-Length and Transfer-Encoding present"},
                400,
                headers={"Connection": "close"},
            )(scope, receive, send)
            return
        for value in content_lengths:
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


class SecurityHeaders:
    """Add defensive response headers to every HTTP response."""

    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"cache-control", b"no-store"),
    ]

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing_keys = {key.lower() for key, _ in headers}
                for key, value in self.HEADERS:
                    if key not in existing_keys:
                        headers.append((key, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)

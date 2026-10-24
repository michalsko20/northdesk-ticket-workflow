"""Observe raw JSON byte limits separately from validated field lengths."""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx


async def run(args):
    suffix = str(time.time_ns())[-12:]
    results = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=15) as client:
        for kind in ("max-unicode", "padded", "padded-chunked", "invalid-large-field"):
            ticket_id = f"raw-{suffix}-{kind}"
            ticket = {
                "ticket_id": ticket_id,
                "customer_id": "C-200",
                "subject": "Password",
                "body": "Forgot password",
            }
            if kind == "max-unicode":
                ticket.update(subject="🧪" * 500, body="🧪" * 20000)
            elif kind == "invalid-large-field":
                ticket["body"] = "x" * (2 * 1024 * 1024)
            body = json.dumps(ticket, ensure_ascii=False).encode()
            if kind.startswith("padded"):
                body = b" " * (2 * 1024 * 1024) + body

            async def chunks(data):
                for offset in range(0, len(data), 65536):
                    yield data[offset : offset + 65536]

            started = time.perf_counter()
            response = await client.post(
                "/tickets",
                content=chunks(body) if kind == "padded-chunked" else body,
                headers={"content-type": "application/json"},
            )
            result = {
                "case": kind,
                "ticket_id": ticket_id,
                "request_bytes": len(body),
                "response_bytes": len(response.content),
                "status": response.status_code,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            results.append(result)
            print(json.dumps(result))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--output", type=Path, default=Path("artifacts/requests.json"))
    asyncio.run(run(parser.parse_args()))

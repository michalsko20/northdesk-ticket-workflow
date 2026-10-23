"""Exercise the seven example tickets through HTTP; only Python's stdlib is needed."""

import argparse
import json
import time
import urllib.request
from pathlib import Path


def request(base_url: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base_url + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--suffix", default="", help="Append to IDs for a fresh demonstration")
    parser.add_argument("--approve-refunds", action="store_true", help="Act as the human approver")
    args = parser.parse_args()
    examples = Path(__file__).resolve().parents[1] / "src/northdesk/data/tickets.json"
    for ticket in json.loads(examples.read_text()):
        ticket["ticket_id"] += args.suffix
        accepted = request(args.base_url, "/tickets", ticket)
        path = f"/tickets/{ticket['ticket_id']}"
        deadline = time.monotonic() + 45
        while True:
            state = request(args.base_url, path)
            if state["step"] == "awaiting_approval":
                if not args.approve_refunds:
                    break
                request(
                    args.base_url,
                    path + "/approval",
                    {
                        "approved": True,
                        "reviewer": "demo-operator",
                    },
                )
            if state["step"] in ("completed", "failed"):
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"Ticket {ticket['ticket_id']} did not settle")
            time.sleep(0.1)
        print(
            json.dumps(
                {
                    "ticket_id": ticket["ticket_id"],
                    "duplicate": accepted["duplicate"],
                    "step": state["step"],
                    "decision": state["decision"],
                    "error": state["error"],
                }
            )
        )
        if state["step"] == "failed":
            raise RuntimeError(f"Ticket failed: {state['error']}")


if __name__ == "__main__":
    main()

"""Minimal CtxTTL client; start the proxy and configure its upstream provider first."""

import os

import httpx

base_url = os.getenv("CTXTTL_EXAMPLE_BASE_URL", "http://127.0.0.1:8765")
model = os.environ["CTXTTL_EXAMPLE_MODEL"]
headers = {
    "X-CtxTTL-Session-ID": "quickstart-session",
    "X-CtxTTL-Task-ID": "quickstart-task",
}

with httpx.Client(base_url=base_url, headers=headers, timeout=60) as client:
    assertion = client.post(
        "/v1/context/items",
        json={
            "kind": "decision",
            "scope": "task",
            "subject": "project.database",
            "value": "PostgreSQL",
            "reason": "quickstart example",
        },
    )
    assertion.raise_for_status()

    completion = client.post(
        "/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Which database did we choose?"}],
        },
    )
    completion.raise_for_status()
    print("trace:", completion.headers.get("X-CtxTTL-Trace-ID"))
    print(completion.json())

"""
Send a test invocation to a locally-running host.

Start the host first in another terminal:
    python -m app.host

Then:
    python -m scripts.invoke_local data.xlsx
"""
from __future__ import annotations

import json
import sys
import urllib.request

PORT = 8088  # AgentServerHost default


def main() -> None:
    filename = sys.argv[1] if len(sys.argv) > 1 else "data.xlsx"
    req = urllib.request.Request(
        f"http://localhost:{PORT}/invocations",
        data=json.dumps({"filename": filename}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print(f"HTTP {resp.status}")
        print(json.dumps(json.loads(resp.read()), indent=2))


if __name__ == "__main__":
    main()

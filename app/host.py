"""
Foundry Hosted Agent — Invocations protocol.

This is the container entrypoint that Azure AI Foundry runs as a *hosted agent*
(your code, on Microsoft-managed infrastructure — no Container Apps involved).

We use the Invocations protocol (`azure-ai-agentserver-invocations`) rather than
the conversational Responses protocol because this task is a custom,
non-conversational request/response: "here is a filename -> here is the list of
CSVs". The Invocations protocol is designed exactly for that and gives us full
control of the request/response body.

Contract exposed to callers (e.g. your Logic App):
    POST /invocations
    body:  { "filename": "data.xlsx" }
    reply: { "status": "succeeded", "sheet_count": 3, "files": [ ... ] }

The host framework also provides GET /readiness for health probes automatically.
"""
from __future__ import annotations

import logging

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .blob_storage import BlobNotFoundError
from .config import configure_logging, get_settings
from .converter import convert_excel_to_csv

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("app.host")

app = InvocationAgentServerHost()


def _extract_filename(payload: object) -> str | None:
    """
    Be tolerant about the incoming shape so the Logic App body is easy to author.
    Accepts:
      {"filename": "..."} | {"blob_name": "..."} | {"file": "..."} |
      {"input": "..."}    | {"input": {"filename": "..."}}
    """
    if isinstance(payload, str):
        return payload.strip() or None
    if not isinstance(payload, dict):
        return None
    for key in ("filename", "blob_name", "file", "name"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    if "input" in payload:
        return _extract_filename(payload["input"])
    return None


def run_conversion(payload: object) -> tuple[int, dict]:
    """
    Pure, framework-free core so it can be unit-tested without the agent server.
    Returns (http_status, response_body).
    """
    filename = _extract_filename(payload)
    if not filename:
        return 400, {
            "status": "failed",
            "error": "Request must include a 'filename' (the Excel blob name).",
        }

    logger.info("Converting '%s'.", filename)
    try:
        result = convert_excel_to_csv(blob_name=filename)
    except BlobNotFoundError as exc:
        logger.warning("Not found: %s", exc)
        return 404, {"status": "failed", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — return a clean error to the caller
        logger.exception("Conversion failed for %s", filename)
        return 500, {"status": "failed", "error": str(exc)}

    return 200, {"status": "succeeded", **result}


@app.invoke_handler  # POST /invocations
async def handle(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(
            {"status": "failed", "error": "Body must be valid JSON."}, status_code=400
        )
    status, body = run_conversion(payload)
    return JSONResponse(body, status_code=status)


if __name__ == "__main__":
    # Validate config early so misconfig fails fast with a clear message.
    settings.validate_runtime()
    # Serves on 0.0.0.0:${PORT:-8088}; Foundry injects PORT at runtime.
    app.run()

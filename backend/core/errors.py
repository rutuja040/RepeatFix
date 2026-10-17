"""Application error handlers.

Design goals:
- Never expose stack traces or internal details to ordinary clients.
- Keep error bodies in a consistent shape: {"detail": <message or structured info>}.
- Log full details server-side for debugging.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("repeatfix.errors")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Full traceback is logged server-side only.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        body: dict[str, Any] = {"detail": "Internal server error"}
        return JSONResponse(status_code=500, content=body)

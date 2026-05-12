from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from fastapi import Request, Response


async def request_context_middleware(request: Request, call_next: Callable):
    """Attach x-request-id and x-process-time-ms headers."""
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    start = time.perf_counter()
    response: Response = await call_next(request)
    cost_ms = (time.perf_counter() - start) * 1000
    response.headers["x-request-id"] = rid
    response.headers["x-process-time-ms"] = f"{cost_ms:.2f}"
    return response

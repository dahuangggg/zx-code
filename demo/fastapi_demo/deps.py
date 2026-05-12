from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException

# Demo-only: hard-coded key.
API_KEY = "demo-key"


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

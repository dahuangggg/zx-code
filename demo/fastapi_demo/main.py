from __future__ import annotations

from fastapi import FastAPI

from .middleware import request_context_middleware
from .routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="FastAPI Demo", version="0.3.0")
    app.middleware("http")(request_context_middleware)
    app.include_router(router)
    return app


app = create_app()

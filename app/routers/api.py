from fastapi import FastAPI
from app.core.deps import get_settings
from app.api.api import api_router


def register(app: FastAPI) -> None:
    app.include_router(api_router, prefix=get_settings().dev.api_str)

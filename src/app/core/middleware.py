from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from asgi_correlation_id import CorrelationIdMiddleware


CORS_ALLOW_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://192.168.93.129:5173",
    "http://192.168.93.129:8000",
    "http://192.168.93.129:8888",
]


def register(app: FastAPI) -> None:
    correlation_id_middleware(app)
    cors_middleware(app)
    trusted_host_middleware(app)


def cors_middleware(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOW_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Conversation-ID", "X-Thread-ID", "Content-Disposition"],
    )


def trusted_host_middleware(app: FastAPI) -> None:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])


def correlation_id_middleware(app: FastAPI) -> None:
    app.add_middleware(CorrelationIdMiddleware)

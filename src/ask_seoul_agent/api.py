"""FastAPI and SSE transport for ASK Seoul Agent."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import __version__
from .agent import AgentRunner
from .clients.ask_seoul import AskSeoulClient
from .models import ChatRequest
from .providers.anthropic import AnthropicMessagesProvider
from .providers.base import LLMProvider
from .providers.demo import DemoProvider
from .providers.gemini import DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
from .providers.gemini import GeminiGenerateContentProvider
from .tools import AskSeoulPort, ToolRegistry
from .weather_catalog import WEATHER_PRODUCTS

ASK_SEOUL_BASE_URL = "https://ask-seoul.kr"


class _Lease:
    """Idempotent semaphore release shared by generator and response background task."""

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._semaphore.release()


def create_app(
    *,
    provider_mode: str = "demo",
    provider: LLMProvider | None = None,
    ask_seoul: AskSeoulPort | None = None,
    anthropic_api_key: str | None = None,
    anthropic_model: str = "claude-sonnet-5",
    gemini_api_key: str | None = None,
    gemini_model: str = GEMINI_DEFAULT_MODEL,
    max_concurrent: int = 2,
) -> FastAPI:
    if provider_mode not in {"demo", "anthropic", "gemini"}:
        raise ValueError("AGENT_PROVIDER must be 'demo', 'anthropic', or 'gemini'")
    if max_concurrent < 1:
        raise ValueError("max_concurrent must be positive")

    owned_http: httpx.AsyncClient | None = None
    if ask_seoul is None:
        owned_http = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            headers={"User-Agent": f"ask-seoul-agent/{__version__}"},
        )
        ask_seoul = AskSeoulClient(base_url=ASK_SEOUL_BASE_URL, http_client=owned_http)

    owned_provider = False
    if provider is None and provider_mode == "demo":
        provider = DemoProvider()
        owned_provider = True
    elif provider is None and provider_mode == "anthropic" and anthropic_api_key:
        provider = AnthropicMessagesProvider(
            api_key=anthropic_api_key,
            model=anthropic_model,
        )
        owned_provider = True
    elif provider is None and provider_mode == "gemini" and gemini_api_key:
        provider = GeminiGenerateContentProvider(
            api_key=gemini_api_key,
            model=gemini_model,
        )
        owned_provider = True

    runner = (
        AgentRunner(
            provider=provider,
            tools=ToolRegistry(ask_seoul=ask_seoul),
            provider_name=provider_mode,
        )
        if provider is not None
        else None
    )
    semaphore = asyncio.Semaphore(max_concurrent)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owned_provider and provider is not None:
                close = getattr(provider, "aclose", None)
                if callable(close):
                    outcome = close()
                    if inspect.isawaitable(outcome):
                        await outcome
            if owned_http is not None:
                await owned_http.aclose()

    app = FastAPI(
        title="ASK Seoul Weather Agent",
        version=__version__,
        description=(
            "Evidence-first, bounded LLM tool calling over four served ASK Seoul weather products."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def defensive_browser_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'none'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "img-src 'self' data: https://fastapi.tiangolo.com; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        )
        return response

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        if runner is None:
            required_secret = {
                "anthropic": "ANTHROPIC_API_KEY",
                "gemini": "GEMINI_API_KEY",
            }.get(provider_mode, "provider credentials")
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not_ready",
                    "provider_mode": provider_mode,
                    "detail": f"{required_secret} is required for {provider_mode} mode.",
                },
            )
        return JSONResponse(
            content={"status": "ready", "provider_mode": provider_mode},
        )

    @app.get("/api/v1/meta")
    async def meta() -> dict[str, Any]:
        return {
            "provider_mode": provider_mode,
            "ready": runner is not None,
            "demo_is_llm": False,
            "tools": ["search_products", "preview_product"],
            "supported_products": [
                {"product_id": product.product_id, "title": product.title}
                for product in WEATHER_PRODUCTS
            ],
        }

    @app.post("/api/v1/chat/stream")
    async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
        if runner is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The configured LLM provider is not ready.",
            )
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=0.05)
        except TimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="The agent concurrency limit is full.",
            ) from exc

        lease = _Lease(semaphore)
        trace_id = uuid4().hex

        async def body() -> AsyncIterator[str]:
            try:
                async for event in runner.stream(payload.question, trace_id=trace_id):
                    if await request.is_disconnected():
                        break
                    yield _encode_sse(event)
            finally:
                lease.release()

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Trace-Id": trace_id,
            },
            background=BackgroundTask(lease.release),
        )

    static_dir = _find_static_dir()
    if static_dir is not None:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    else:

        @app.get("/")
        async def root() -> dict[str, str]:
            return {
                "service": "ask-seoul-agent",
                "docs": "/docs",
                "ui": "Run npm build in frontend/ to serve the UI here.",
            }

    return app


def _encode_sse(event: dict[str, Any]) -> str:
    event_name = str(event.get("event", "message"))
    payload = json.dumps(
        jsonable_encoder(event),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"event: {event_name}\ndata: {payload}\n\n"


def _find_static_dir() -> Path | None:
    package_dir = Path(__file__).resolve().parent
    candidates = [
        package_dir / "static",
        package_dir.parents[1] / "frontend" / "dist",
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def app_from_environment() -> FastAPI:
    return create_app(
        provider_mode=os.getenv("AGENT_PROVIDER", "demo").strip().lower(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", GEMINI_DEFAULT_MODEL),
        max_concurrent=int(os.getenv("AGENT_MAX_CONCURRENT", "2")),
    )

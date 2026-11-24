"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.routes import chat, connections, metrics, queries, schema, stream, suggestions
from app.collector.scheduler import manager
from app.config import get_settings
from app.db.meta import dispose_engine, init_schema, session_scope
from app.db.models import Connection, ConnectionKind
from app.db.target import probe, registry
from app.llm.registry import close_provider, get_provider

log = logging.getLogger(__name__)


async def _register_sandbox() -> None:
    """Register the bundled sandbox on first boot so the UI is never empty."""
    settings = get_settings()
    if not (settings.sandbox_auto_register and settings.sandbox_dsn):
        return

    async with session_scope() as session:
        existing = (
            await session.execute(
                select(Connection).where(Connection.kind == ConnectionKind.sandbox)
            )
        ).scalars().first()
        if existing is not None:
            return

        result = await probe(settings.sandbox_dsn)
        if not result.get("ok"):
            log.warning("sandbox not reachable yet, skipping auto-registration: %s",
                        result.get("error"))
            return

        session.add(
            Connection(
                name="Sandbox (shopdb)",
                kind=ConnectionKind.sandbox,
                dsn=settings.sandbox_dsn,
                apply_dsn=settings.sandbox_apply_dsn,
                # Safe here and nowhere else: the sandbox is disposable, and
                # EXPLAIN ANALYZE is most of the point of having one.
                allow_explain_analyze=True,
                server_version=result.get("server_version"),
                extensions=result.get("extensions") or {},
            )
        )
        log.info("registered bundled sandbox")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    await init_schema()
    await _register_sandbox()
    get_provider()  # construct eagerly so misconfiguration is logged at boot
    await manager.start_all()
    log.info("ready")

    try:
        yield
    finally:
        await manager.stop_all()
        await registry.close_all()
        await close_provider()
        await dispose_engine()
        log.info("shut down cleanly")


settings = get_settings()

app = FastAPI(
    title="Database Optimizer",
    version="0.1.0",
    description=(
        "PostgreSQL monitoring and optimization.\n\n"
        "Reads are performed with a read-only credential under enforced statement and "
        "lock timeouts. Changes are proposed, never applied automatically: every "
        "executable statement is parsed against an allowlist and runs only after an "
        "explicit human approval, with an audit record written before execution."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = app.router

app.include_router(connections.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")
app.include_router(queries.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
app.include_router(suggestions.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(stream.router, prefix="/api")


@app.get("/api/health", tags=["meta"])
async def service_health():
    return {
        "status": "ok",
        "collectors": manager.status(),
        "llm_provider": get_provider().name,
        "llm_enabled": get_settings().llm_enabled,
    }


@app.get("/api/config", tags=["meta"])
async def config():
    """Non-secret configuration, so the UI can explain its own behaviour."""
    s = get_settings()
    return {
        "poll_interval_seconds": s.poll_interval_seconds,
        "snapshot_retention_hours": s.snapshot_retention_hours,
        "statement_timeout_ms": s.statement_timeout_ms,
        "lock_timeout_ms": s.lock_timeout_ms,
        "target_pool_max": s.target_pool_max,
        "redact_literals": s.redact_literals,
        "llm_provider": s.llm_provider,
        "llm_enabled": s.llm_enabled,
    }

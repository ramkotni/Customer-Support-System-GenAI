"""FastAPI application factory (Category 1 — Tool APIs)."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.deps import get_principal
from app.api.v1.router import api_router
from app.api.v1.routers import health as health_router
from app.core.config import settings
from app.core.correlation import REQUEST_ID_HEADER, CorrelationIdMiddleware
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import RateLimitMiddleware
from app.db.migrations import ensure_chat_schema
from app.db.session import dispose_engine, get_engine, ping

logger = get_logger("app.main")

_DESCRIPTION = """
Agentic Customer Support System — **Category 1: Tool APIs**.

Secure, read-mostly REST endpoints over the commerce database that the support
agent (added later) will consume as tools:

* **customers** — profile, orders, addresses, store credit
* **orders** — details, status, tracking, items, payment, history, invoice,
  delivery estimate, and refund/return/cancellation **eligibility** (read-only)
* **order-actions** — refunds, returns, claims, cancellation, goodwill credit
  (writes; idempotent; policy + confirmation enforced)
* **products** — catalog
* **tickets** — support tickets
* **conversations** — chat transcript for the support UI: conversations,
  turns, messages, and a streaming (SSE) answer endpoint

The conversation endpoints answer from a **placeholder responder** for now; the
agent harness replaces it in a later phase without changing the contract.

Auth: send `X-API-Key`. Act on behalf of a customer with `X-Customer-Id`
(customer-ownership is enforced on every order/customer resource).
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    get_engine()
    db_ok = await ping()
    if db_ok:
        # The API owns the conversation tables; create them if absent.
        await ensure_chat_schema()
    logger.info("startup", extra={"db_ok": db_ok,
                                  "db_scheme": settings.effective_database_url.split("://")[0]})
    yield
    await dispose_engine()
    logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=_DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "customers", "description": "Customer profile, orders, addresses, store credit."},
            {"name": "orders", "description": "Order details, tracking, payments, eligibility (read-only)."},
            {"name": "order-actions", "description": "Refunds, returns, claims, cancellation (writes)."},
            {"name": "products", "description": "Product catalog."},
            {"name": "tickets", "description": "Support tickets."},
            {"name": "conversations",
             "description": "Chat conversations, turns, messages, and streaming answers."},
            {"name": "health", "description": "Liveness/health."},
        ],
    )

    # Middleware (outermost first): CORS, correlation id, then rate limiting.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    if settings.cors_origin_list:
        # The Vite dev server proxies /api, so CORS is only needed when the
        # frontend is served from a different origin than the API.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=[REQUEST_ID_HEADER],
        )

    register_exception_handlers(app)

    app.include_router(health_router.router)
    # Every /api/v1 route requires a valid API key principal.
    app.include_router(api_router, prefix=settings.api_v1_prefix,
                       dependencies=[Depends(get_principal)])
    return app


app = create_app()

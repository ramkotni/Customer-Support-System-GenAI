"""Aggregate the v1 API surface.

Category 1 — Tool APIs (backend business APIs), plus the conversation APIs
that back the chat UI. The agent orchestration layer (Category 2) will be
added in a later phase; until then the chat endpoints answer from a
placeholder responder (see app/services/chat_responder.py).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import (
    actions,
    conversations,
    customers,
    orders,
    products,
    tickets,
)

api_router = APIRouter()
api_router.include_router(customers.router)
api_router.include_router(orders.router)
api_router.include_router(actions.router)
api_router.include_router(products.router)
api_router.include_router(tickets.router)
api_router.include_router(conversations.router)

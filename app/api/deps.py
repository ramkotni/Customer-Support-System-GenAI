"""FastAPI dependencies: auth principal, DB session, services, ownership, idempotency."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    API_KEY_HEADER,
    CUSTOMER_ID_HEADER,
    Principal,
    authenticate,
    ensure_owns_customer,
)
from app.db.session import get_session
from app.repositories.conversations_repo import ConversationsRepository
from app.repositories.customers_repo import CustomersRepository
from app.repositories.orders_repo import OrdersRepository
from app.repositories.products_repo import ProductsRepository
from app.repositories.resolutions_repo import ResolutionsRepository
from app.repositories.support_repo import SupportRepository
from app.services.chat_responder import ChatResponder, StubChatResponder
from app.services.conversation_service import ConversationService
from app.services.customer_service import CustomerService
from app.services.order_service import OrderService
from app.services.product_service import ProductService
from app.services.resolution_service import ResolutionService
from app.services.ticket_service import TicketService
from app.core.config import settings


async def get_principal(
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
    x_customer_id: Annotated[str | None, Header(alias=CUSTOMER_ID_HEADER)] = None,
) -> Principal:
    return authenticate(x_api_key, x_customer_id)


def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    return idempotency_key


DbSession = Annotated[AsyncSession, Depends(get_session)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


# --------------------------- service factories ---------------------------
def get_chat_responder() -> ChatResponder:
    """The chat reply generator.

    PHASE 3: swap ``StubChatResponder`` for the agent harness here. It is the
    only line that needs to change — the responder is a one-method protocol
    (see app/services/chat_responder.py) and everything downstream, including
    the SSE transport and the frontend, already consumes it.
    """
    return StubChatResponder()


ChatResponderDep = Annotated[ChatResponder, Depends(get_chat_responder)]


def get_conversation_service(
    db: DbSession, responder: ChatResponderDep
) -> ConversationService:
    return ConversationService(ConversationsRepository(db), responder)


def get_customer_service(db: DbSession) -> CustomerService:
    return CustomerService(CustomersRepository(db))


def get_order_service(db: DbSession) -> OrderService:
    return OrderService(OrdersRepository(db))


def get_product_service(db: DbSession) -> ProductService:
    return ProductService(ProductsRepository(db))


def get_resolution_service(db: DbSession) -> ResolutionService:
    return ResolutionService(
        OrdersRepository(db), ResolutionsRepository(db), SupportRepository(db),
        approval_limit_cents=settings.agent_refund_approval_limit_cents,
    )


def get_ticket_service(db: DbSession) -> TicketService:
    return TicketService(SupportRepository(db), OrdersRepository(db))


ConversationSvc = Annotated[ConversationService, Depends(get_conversation_service)]
CustomerSvc = Annotated[CustomerService, Depends(get_customer_service)]
OrderSvc = Annotated[OrderService, Depends(get_order_service)]
ProductSvc = Annotated[ProductService, Depends(get_product_service)]
ResolutionSvc = Annotated[ResolutionService, Depends(get_resolution_service)]
TicketSvc = Annotated[TicketService, Depends(get_ticket_service)]


# --------------------------- ownership guard ---------------------------
async def get_owned_order(order_id: int, principal: CurrentPrincipal, db: DbSession) -> dict:
    """Load an order and enforce that the caller is allowed to see this customer's data."""
    from app.core.exceptions import NotFoundError
    owner = await OrdersRepository(db).get_owner(order_id)
    if not owner:
        raise NotFoundError("Order not found.", details={"order_id": order_id})
    ensure_owns_customer(principal, owner["customer_id"])
    return owner


OwnedOrder = Annotated[dict, Depends(get_owned_order)]


async def ensure_customer_access(customer_id: int, principal: CurrentPrincipal) -> int:
    ensure_owns_customer(principal, customer_id)
    return customer_id


CustomerAccess = Annotated[int, Depends(ensure_customer_access)]


async def require_customer_scope(principal: CurrentPrincipal) -> int:
    """The effective customer id, or a 422 if the caller sent no scope.

    Conversations are per-customer, so `X-Customer-Id` is mandatory for them —
    the same rule the tickets API applies.
    """
    from app.core.exceptions import ValidationError
    if principal.customer_id is None:
        raise ValidationError(
            "A customer scope (X-Customer-Id) is required for conversations.",
            code="customer_scope_required",
        )
    return principal.customer_id


ScopedCustomer = Annotated[int, Depends(require_customer_scope)]

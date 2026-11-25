"""Conversation / chat APIs backing the support chat UI.

These are the endpoints the frontend talks to:

    POST   /conversations                          open a conversation
    GET    /conversations                          sidebar history (paginated)
    GET    /conversations/{id}                     full transcript
    PATCH  /conversations/{id}                     rename
    DELETE /conversations/{id}                     delete (cascades)
    GET    /conversations/{id}/messages            flat message list (paginated)
    POST   /conversations/{id}/messages            ask — returns the whole turn
    POST   /conversations/{id}/messages/stream     ask — streams the answer (SSE)
    DELETE /conversations/{id}/turns/{turn_id}     drop a turn (UI "Retry")

Every conversation belongs to a customer, so a customer scope (`X-Customer-Id`)
is required — the same rule the tickets API applies.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from app.api.deps import ChatResponderDep, ConversationSvc, CurrentPrincipal, ScopedCustomer
from app.core.exceptions import NotFoundError
from app.core.pagination import PageParams, page_params, resolve_sort
from app.core.security import ensure_owns_customer
from app.schemas.common import Page, make_page
from app.schemas.conversation import (
    ConversationCreate,
    ConversationOut,
    ConversationSummaryOut,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
    TurnOut,
)
from app.services.chat_stream import stream_turn

router = APIRouter(prefix="/conversations", tags=["conversations"])

_CONVERSATION_SORTS = {
    "updated_at": "c.updated_at",
    "created_at": "c.created_at",
    "title": "c.title",
}

# Streaming responses must not be buffered by an intermediary, or the UI only
# repaints once the whole answer has arrived.
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def get_owned_conversation(
    conversation_id: str, principal: CurrentPrincipal, svc: ConversationSvc
) -> dict:
    """Load a conversation and enforce that the caller owns it.

    Mirrors ``get_owned_order``: a 404 for a conversation that does not exist,
    a 403 for one that belongs to someone else.
    """
    convo = await svc.repo.get_conversation(conversation_id)
    if not convo:
        raise NotFoundError(
            "Conversation not found.", details={"conversation_id": conversation_id}
        )
    ensure_owns_customer(principal, convo["customer_id"])
    return convo


OwnedConversation = Annotated[dict, Depends(get_owned_conversation)]


# ------------------------------- transcript --------------------------------

@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED,
             summary="Create a conversation")
async def create_conversation(
    body: ConversationCreate, svc: ConversationSvc, customer_id: ScopedCustomer
) -> ConversationOut:
    return await svc.create(
        customer_id=customer_id, title=body.title, first_message=body.first_message
    )


@router.get("", response_model=Page[ConversationSummaryOut],
            summary="List conversations (sidebar history)")
async def list_conversations(
    svc: ConversationSvc,
    customer_id: ScopedCustomer,
    page: Annotated[PageParams, Depends(page_params)],
) -> Page[ConversationSummaryOut]:
    order_by = resolve_sort(page, _CONVERSATION_SORTS, "c.updated_at DESC, c.rowid DESC")
    items, total = await svc.list_for_customer(
        customer_id=customer_id, limit=page.limit, offset=page.offset, order_by=order_by
    )
    return make_page(items, total, page.limit, page.offset)


@router.get("/{conversation_id}", response_model=ConversationOut,
            summary="Retrieve a conversation with its full transcript")
async def get_conversation(
    convo: OwnedConversation, svc: ConversationSvc
) -> ConversationOut:
    return await svc.get_detail(convo["conversation_id"])


@router.patch("/{conversation_id}", response_model=ConversationOut,
              summary="Rename a conversation")
async def rename_conversation(
    convo: OwnedConversation, body: ConversationUpdate, svc: ConversationSvc
) -> ConversationOut:
    return await svc.rename(convo["conversation_id"], body.title)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a conversation and its transcript")
async def delete_conversation(convo: OwnedConversation, svc: ConversationSvc) -> None:
    await svc.delete(convo["conversation_id"])


@router.get("/{conversation_id}/messages", response_model=Page[MessageOut],
            summary="List the messages in a conversation")
async def list_messages(
    convo: OwnedConversation,
    svc: ConversationSvc,
    page: Annotated[PageParams, Depends(page_params)],
) -> Page[MessageOut]:
    items, total = await svc.list_messages(
        convo["conversation_id"], limit=page.limit, offset=page.offset
    )
    return make_page(items, total, page.limit, page.offset)


# --------------------------------- asking ----------------------------------

@router.post("/{conversation_id}/messages", response_model=TurnOut,
             status_code=status.HTTP_201_CREATED,
             summary="Ask a question and wait for the complete answer")
async def create_message(
    convo: OwnedConversation, body: MessageCreate, svc: ConversationSvc
) -> TurnOut:
    return await svc.ask(convo["conversation_id"], body.content)


@router.post(
    "/{conversation_id}/messages/stream",
    summary="Ask a question and stream the answer (text/event-stream)",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE stream of `start`, `delta`, `meta` and `done` events.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_message(
    convo: OwnedConversation, body: MessageCreate, responder: ChatResponderDep
) -> StreamingResponse:
    # Ownership is already enforced above. The generator opens its own DB
    # sessions, because this request's session is closed before the body is
    # consumed (see app/services/chat_stream.py).
    return StreamingResponse(
        stream_turn(
            conversation_id=convo["conversation_id"],
            question=body.content,
            responder=responder,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.delete("/{conversation_id}/turns/{turn_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a turn (used by the UI's Retry)")
async def delete_turn(
    convo: OwnedConversation, turn_id: str, svc: ConversationSvc
) -> None:
    await svc.delete_turn(convo["conversation_id"], turn_id)

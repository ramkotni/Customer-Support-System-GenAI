"""Server-Sent Events transport for a chat turn.

Why this module owns its own database sessions
----------------------------------------------
FastAPI closes ``yield`` dependencies (including the request's DB session)
*before* a ``StreamingResponse`` body is consumed. Touching the request session
from inside the generator would therefore fail. Every statement below runs in a
session this module opens itself, from the shared sessionmaker.

Wire format — one frame per event, UTF-8, ``\\n\\n`` terminated:

    event: start    data: {conversationId, title, turnId, humanMessageId, aiMessageId}
    event: delta    data: {text}                      (zero or more)
    event: meta     data: {meta: {tools, citations, escalated}}
    event: done     data: {turnId, status, aiMessageId}
    event: error    data: {code, message, requestId}  (terminal, instead of done)

A turn is persisted whatever happens: on success the answer is stored complete,
and if the client disconnects mid-stream or generation raises, the partial text
is kept and the turn is marked ``error`` so the UI can offer Retry.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

from app.core.correlation import get_request_id
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.repositories.conversations_repo import ConversationsRepository
from app.services.chat_responder import ChatResponder, ResponseMeta
from app.services.conversation_service import (
    FAILED_MESSAGE,
    ConversationService,
    meta_to_json,
)

logger = get_logger("app.chat_stream")

STOPPED_MESSAGE = "Generation stopped."


def frame(event: str, payload: dict) -> str:
    """Encode one SSE frame. JSON escaping keeps newlines out of `data:`."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def stream_turn(
    *, conversation_id: str, question: str, responder: ChatResponder
) -> AsyncIterator[str]:
    """Run one turn, yielding SSE frames as the answer is produced."""
    maker = get_sessionmaker()

    # ---- 1. Open the turn (its own transaction, committed before streaming) --
    async with maker() as session:
        service = ConversationService(ConversationsRepository(session), responder)
        convo = await service.require(conversation_id)
        turn_id, human_id, ai_id = await service.open_turn(convo, question)
        history = await service.build_history(conversation_id, exclude_turn_id=turn_id)
        customer_id = convo["customer_id"]
        # Re-read: open_turn may have derived the title from this question.
        refreshed = await service.repo.get_conversation(conversation_id)
        title = (refreshed or convo)["title"]
        await session.commit()

    # Opening comment flushes headers immediately, so the client's reader starts.
    yield ": open\n\n"
    yield frame(
        "start",
        {
            "conversationId": conversation_id,
            "title": title,
            "turnId": turn_id,
            "humanMessageId": human_id,
            "aiMessageId": ai_id,
        },
    )

    chunks: list[str] = []
    meta: ResponseMeta | None = None

    # ---- 2. Stream the answer ----------------------------------------------
    try:
        async for chunk in responder.respond(
            question=question, history=history, customer_id=customer_id
        ):
            if chunk.text:
                chunks.append(chunk.text)
                yield frame("delta", {"text": chunk.text})
            if chunk.meta:
                meta = chunk.meta

    except asyncio.CancelledError:
        # The client went away (usually the Stop button). Keep the partial
        # answer, then let the cancellation propagate. The write is shielded so
        # it survives the cancellation that is already in flight.
        task = asyncio.create_task(
            _finalize(conversation_id, turn_id, ai_id, "".join(chunks), None,
                      status="error", error=STOPPED_MESSAGE)
        )
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)
        raise

    except AppError as exc:
        await _finalize(conversation_id, turn_id, ai_id, "".join(chunks), None,
                        status="error", error=exc.message)
        yield frame("error", {"code": exc.code, "message": exc.message,
                              "requestId": get_request_id()})
        return

    except Exception:
        logger.exception("chat_stream_failed", extra={"conversation_id": conversation_id})
        await _finalize(conversation_id, turn_id, ai_id, "".join(chunks), None,
                        status="error", error=FAILED_MESSAGE)
        # Never leak internal exception text to the client.
        yield frame("error", {"code": "internal_error", "message": FAILED_MESSAGE,
                              "requestId": get_request_id()})
        return

    # ---- 3. Persist the completed turn -------------------------------------
    await _finalize(conversation_id, turn_id, ai_id, "".join(chunks), meta,
                    status="complete", error=None)

    if meta is not None:
        yield frame(
            "meta",
            {"meta": {"tools": meta.tools, "citations": meta.citations,
                      "escalated": meta.escalated}},
        )
    yield frame("done", {"turnId": turn_id, "status": "complete", "aiMessageId": ai_id})


async def _finalize(
    conversation_id: str,
    turn_id: str,
    ai_message_id: str,
    content: str,
    meta: ResponseMeta | None,
    *,
    status: str,
    error: str | None,
) -> None:
    """Write the answer and close out the turn, in a session of its own."""
    maker = get_sessionmaker()
    try:
        async with maker() as session:
            repo = ConversationsRepository(session)
            await repo.update_message(
                ai_message_id, content=content, meta_json=meta_to_json(meta)
            )
            await repo.set_turn_status(turn_id, status, error)
            await repo.touch(conversation_id)
            await session.commit()
    except Exception:  # pragma: no cover - last-resort safety net
        # Losing the transcript write must not mask the original failure.
        logger.exception("chat_turn_finalize_failed", extra={"turn_id": turn_id})

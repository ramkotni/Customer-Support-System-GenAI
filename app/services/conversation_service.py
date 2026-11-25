"""Conversation business logic: transcript CRUD and the chat turn lifecycle.

A *turn* is one exchange — a human question plus the AI answer to it — and is
the unit of work here:

    pending  -> the question is stored, the answer is being generated
    complete -> both messages are stored
    error    -> generation failed or the client stopped it; partial text kept

Only ``complete`` turns become conversation context, so a failed answer never
poisons the next question.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from app.core.exceptions import NotFoundError, UpstreamError
from app.core.logging import get_logger
from app.repositories.conversations_repo import ConversationsRepository
from app.schemas.conversation import (
    ConversationOut,
    ConversationSummaryOut,
    MessageOut,
    TurnOut,
    meta_out,
)
from app.services.chat_responder import ChatResponder, ContextMessage, ResponseMeta

logger = get_logger("app.conversations")

DEFAULT_TITLE = "New conversation"
FAILED_MESSAGE = "The assistant could not finish this answer."
_MAX_TITLE = 48


def derive_title(question: str) -> str:
    """Build a plain-text conversation title from the customer's first question.

    Markdown markers are stripped throughout rather than only at the start, so
    ``**Refund me** please`` can't leave a lopsided ``Refund me** please``.
    Mirrors ``frontend/src/lib/title.ts`` so client and server agree.
    """
    cleaned = question
    cleaned = re.sub(r"`+", "", cleaned)
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"_{2,}", "", cleaned)
    cleaned = re.sub(r"^[\s\"'“”>#\-–—]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return DEFAULT_TITLE
    if len(cleaned) <= _MAX_TITLE:
        return cleaned

    cut = cleaned[:_MAX_TITLE]
    last_space = cut.rfind(" ")
    base = cut[:last_space] if last_space > _MAX_TITLE // 2 else cut
    return re.sub(r"[.,;:!?\s]+$", "", base) + "…"


def meta_to_json(meta: ResponseMeta | None) -> str | None:
    if meta is None:
        return None
    return json.dumps(
        {"tools": meta.tools, "citations": meta.citations, "escalated": meta.escalated}
    )


def _message_out(row: dict) -> MessageOut:
    raw = row.get("meta_json")
    try:
        parsed = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        parsed = None
    return MessageOut(
        id=row["message_id"],
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
        meta=meta_out(parsed),
    )


class ConversationService:
    def __init__(self, repo: ConversationsRepository, responder: ChatResponder) -> None:
        self.repo = repo
        self.responder = responder

    # ------------------------------ transcript ------------------------------
    async def create(
        self, *, customer_id: int, title: str | None, first_message: str | None
    ) -> ConversationOut:
        """Open a conversation. If ``first_message`` is given, the title is
        derived from it so the client never has to send one."""
        resolved = (title or "").strip()
        if not resolved and first_message:
            resolved = derive_title(first_message)
        row = await self.repo.create_conversation(
            customer_id=customer_id, title=resolved or DEFAULT_TITLE
        )
        return ConversationOut(
            id=row["conversation_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turns=[],
        )

    async def list_for_customer(
        self, *, customer_id: int, limit: int, offset: int, order_by: str
    ) -> tuple[list[ConversationSummaryOut], int]:
        rows = await self.repo.list_conversations(
            customer_id=customer_id, limit=limit, offset=offset, order_by=order_by
        )
        total = await self.repo.count_conversations(customer_id=customer_id)
        items = [
            ConversationSummaryOut(
                id=r["conversation_id"],
                title=r["title"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                turn_count=r["turn_count"],
            )
            for r in rows
        ]
        return items, total

    async def get_detail(self, conversation_id: str) -> ConversationOut:
        convo = await self.require(conversation_id)
        return ConversationOut(
            id=convo["conversation_id"],
            title=convo["title"],
            created_at=convo["created_at"],
            updated_at=convo["updated_at"],
            turns=await self._turns(conversation_id),
        )

    async def rename(self, conversation_id: str, title: str) -> ConversationOut:
        await self.require(conversation_id)
        await self.repo.update_title(conversation_id, title.strip())
        return await self.get_detail(conversation_id)

    async def delete(self, conversation_id: str) -> None:
        await self.require(conversation_id)
        await self.repo.delete_conversation(conversation_id)

    async def list_messages(
        self, conversation_id: str, *, limit: int, offset: int
    ) -> tuple[list[MessageOut], int]:
        await self.require(conversation_id)
        rows = await self.repo.list_messages_for_conversation(
            conversation_id, limit=limit, offset=offset
        )
        total = await self.repo.count_messages(conversation_id)
        return [_message_out(r) for r in rows], total

    async def delete_turn(self, conversation_id: str, turn_id: str) -> None:
        """Drop a turn — how the UI's Retry discards a failed exchange."""
        await self.require(conversation_id)
        turn = await self.repo.get_turn(turn_id)
        if not turn or turn["conversation_id"] != conversation_id:
            raise NotFoundError("Turn not found.", details={"turn_id": turn_id})
        await self.repo.delete_turn(turn_id)
        await self.repo.touch(conversation_id)

    # -------------------------------- asking --------------------------------
    async def ask(self, conversation_id: str, question: str) -> TurnOut:
        """Non-streaming ask: run the responder to completion, return the turn."""
        convo = await self.require(conversation_id)
        turn_id, human_id, ai_id = await self.open_turn(convo, question)

        chunks: list[str] = []
        meta: ResponseMeta | None = None
        history = await self.build_history(conversation_id, exclude_turn_id=turn_id)

        try:
            async for chunk in self.responder.respond(
                question=question, history=history, customer_id=convo["customer_id"]
            ):
                if chunk.text:
                    chunks.append(chunk.text)
                if chunk.meta:
                    meta = chunk.meta
        except Exception:
            # Keep whatever was produced and mark the turn failed, so the UI can
            # offer Retry and the broken exchange never becomes context.
            logger.exception("chat_turn_failed", extra={"conversation_id": conversation_id})
            await self.repo.update_message(
                ai_id, content="".join(chunks), meta_json=None
            )
            await self.repo.set_turn_status(turn_id, "error", FAILED_MESSAGE)
            await self.repo.touch(conversation_id)
            # Commit explicitly: we are about to raise, and `get_session` rolls
            # back on exception — which would also discard the question and the
            # failure record we just wrote. The turn must survive so the UI can
            # show it with a Retry.
            await self.repo.session.commit()
            # Surface a clean envelope rather than letting it reach the
            # catch-all handler; never echo the underlying exception text.
            raise UpstreamError(FAILED_MESSAGE, code="assistant_failed") from None

        await self.repo.update_message(ai_id, content="".join(chunks), meta_json=meta_to_json(meta))
        await self.repo.set_turn_status(turn_id, "complete")
        await self.repo.touch(conversation_id)

        turns = await self._turns(conversation_id)
        found = next((t for t in turns if t.id == turn_id), None)
        if found is None:  # pragma: no cover - defensive
            raise NotFoundError("Turn not found.", details={"turn_id": turn_id})
        del human_id
        return found

    async def build_history(
        self, conversation_id: str, *, exclude_turn_id: str | None = None
    ) -> list[ContextMessage]:
        """Prior turns as agent context, oldest first.

        Only ``complete`` turns are included: a pending or failed exchange is
        not context. Mirrors ``buildHistory`` in the frontend reducer.
        """
        history: list[ContextMessage] = []
        rows = await self.repo.list_messages_for_conversation(conversation_id)
        by_turn: dict[str, list[dict]] = {}
        for row in rows:
            by_turn.setdefault(row["turn_id"], []).append(row)

        for turn in await self.repo.list_turns(conversation_id):
            tid = turn["turn_id"]
            if tid == exclude_turn_id or turn["status"] != "complete":
                continue
            messages = by_turn.get(tid, [])
            human = next((m for m in messages if m["role"] == "human"), None)
            ai = next((m for m in messages if m["role"] == "ai"), None)
            if not human or not ai or not ai["content"]:
                continue
            history.append(
                ContextMessage(role="human", content=human["content"],
                               message_id=human["message_id"])
            )
            history.append(
                ContextMessage(role="ai", content=ai["content"], message_id=ai["message_id"])
            )
        return history

    # --------------------------- shared with chat_stream ---------------------
    async def require(self, conversation_id: str) -> dict:
        convo = await self.repo.get_conversation(conversation_id)
        if not convo:
            raise NotFoundError(
                "Conversation not found.", details={"conversation_id": conversation_id}
            )
        return convo

    async def open_turn(self, convo: dict, question: str) -> tuple[str, str, str]:
        """Create a pending turn, store the question, pre-allocate the answer.

        The AI message is created empty so its Message ID is known before
        generation starts — the stream announces it in its ``start`` frame.
        """
        conversation_id = convo["conversation_id"]
        seq = await self.repo.next_seq(conversation_id)
        turn_id = await self.repo.create_turn(conversation_id=conversation_id, seq=seq)
        human_id = await self.repo.add_message(
            conversation_id=conversation_id, turn_id=turn_id, role="human", content=question
        )
        ai_id = await self.repo.add_message(
            conversation_id=conversation_id, turn_id=turn_id, role="ai", content=""
        )

        # The first question names the conversation, unless it was titled already.
        if seq == 1 and (convo["title"] or DEFAULT_TITLE) == DEFAULT_TITLE:
            await self.repo.update_title(conversation_id, derive_title(question))
        else:
            await self.repo.touch(conversation_id)

        return turn_id, human_id, ai_id

    async def _turns(self, conversation_id: str) -> list[TurnOut]:
        rows = await self.repo.list_messages_for_conversation(conversation_id)
        by_turn: dict[str, list[dict]] = {}
        for row in rows:
            by_turn.setdefault(row["turn_id"], []).append(row)

        out: list[TurnOut] = []
        for turn in await self.repo.list_turns(conversation_id):
            messages = by_turn.get(turn["turn_id"], [])
            human = next((m for m in messages if m["role"] == "human"), None)
            ai = next((m for m in messages if m["role"] == "ai"), None)
            if human is None:  # pragma: no cover - a turn always has its question
                continue
            # An empty answer is still "not answered yet" as far as the UI goes.
            ai_out = _message_out(ai) if ai and ai["content"] else None
            out.append(
                TurnOut(
                    id=turn["turn_id"],
                    status=turn["status"],
                    human=_message_out(human),
                    ai=ai_out,
                    error=turn["error"],
                )
            )
        return out

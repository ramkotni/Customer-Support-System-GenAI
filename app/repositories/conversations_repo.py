"""Conversations, turns, and chat messages. All SQL for the chat transcript."""
from __future__ import annotations

import uuid

from app.repositories.base import Repository


def _oid(prefix: str) -> str:
    """Opaque public id, e.g. ``conv_4a91de02bb17``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


new_conversation_id = lambda: _oid("conv")   # noqa: E731
new_turn_id = lambda: _oid("turn")           # noqa: E731
new_message_id = lambda: _oid("msg")         # noqa: E731


class ConversationsRepository(Repository):
    # ----------------------------- conversations -----------------------------
    async def create_conversation(self, *, customer_id: int, title: str) -> dict:
        cid = new_conversation_id()
        await self.execute(
            """
            INSERT INTO conversations(conversation_id, customer_id, title)
            VALUES (:cid, :cust, :title)
            """,
            {"cid": cid, "cust": customer_id, "title": title},
        )
        row = await self.get_conversation(cid)
        assert row is not None  # just inserted
        return row

    async def get_conversation(self, conversation_id: str) -> dict | None:
        return await self.fetch_one(
            """
            SELECT conversation_id, customer_id, title, status, created_at, updated_at
            FROM conversations
            WHERE conversation_id = :cid
            """,
            {"cid": conversation_id},
        )

    async def list_conversations(
        self, *, customer_id: int, limit: int, offset: int, order_by: str
    ) -> list[dict]:
        # `order_by` is whitelist-resolved by core.pagination.resolve_sort.
        return await self.fetch_all(
            f"""
            SELECT c.conversation_id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM conversation_turns t
                     WHERE t.conversation_id = c.conversation_id) AS turn_count
            FROM conversations c
            WHERE c.customer_id = :cust AND c.status = 'active'
            ORDER BY {order_by}
            LIMIT :limit OFFSET :offset
            """,
            {"cust": customer_id, "limit": limit, "offset": offset},
        )

    async def count_conversations(self, *, customer_id: int) -> int:
        return int(
            await self.scalar(
                "SELECT COUNT(*) FROM conversations "
                "WHERE customer_id = :cust AND status = 'active'",
                {"cust": customer_id},
            )
        )

    async def update_title(self, conversation_id: str, title: str) -> None:
        await self.execute(
            """
            UPDATE conversations
            SET title = :title, updated_at = datetime('now')
            WHERE conversation_id = :cid
            """,
            {"cid": conversation_id, "title": title},
        )

    async def touch(self, conversation_id: str) -> None:
        """Bump ``updated_at`` so the sidebar orders by recent activity."""
        await self.execute(
            "UPDATE conversations SET updated_at = datetime('now') WHERE conversation_id = :cid",
            {"cid": conversation_id},
        )

    async def delete_conversation(self, conversation_id: str) -> None:
        # Turns and messages cascade (PRAGMA foreign_keys is ON per connection).
        await self.execute(
            "DELETE FROM conversations WHERE conversation_id = :cid",
            {"cid": conversation_id},
        )

    # --------------------------------- turns ---------------------------------
    async def next_seq(self, conversation_id: str) -> int:
        return int(
            await self.scalar(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM conversation_turns "
                "WHERE conversation_id = :cid",
                {"cid": conversation_id},
            )
        )

    async def create_turn(self, *, conversation_id: str, seq: int) -> str:
        turn_id = new_turn_id()
        await self.execute(
            """
            INSERT INTO conversation_turns(turn_id, conversation_id, seq, status)
            VALUES (:tid, :cid, :seq, 'pending')
            """,
            {"tid": turn_id, "cid": conversation_id, "seq": seq},
        )
        return turn_id

    async def get_turn(self, turn_id: str) -> dict | None:
        return await self.fetch_one(
            """
            SELECT turn_id, conversation_id, seq, status, error, created_at
            FROM conversation_turns
            WHERE turn_id = :tid
            """,
            {"tid": turn_id},
        )

    async def set_turn_status(self, turn_id: str, status: str, error: str | None = None) -> None:
        await self.execute(
            "UPDATE conversation_turns SET status = :status, error = :error WHERE turn_id = :tid",
            {"tid": turn_id, "status": status, "error": error},
        )

    async def delete_turn(self, turn_id: str) -> None:
        await self.execute(
            "DELETE FROM conversation_turns WHERE turn_id = :tid", {"tid": turn_id}
        )

    async def list_turns(self, conversation_id: str) -> list[dict]:
        return await self.fetch_all(
            """
            SELECT turn_id, seq, status, error, created_at
            FROM conversation_turns
            WHERE conversation_id = :cid
            ORDER BY seq
            """,
            {"cid": conversation_id},
        )

    async def count_turns(self, conversation_id: str) -> int:
        return int(
            await self.scalar(
                "SELECT COUNT(*) FROM conversation_turns WHERE conversation_id = :cid",
                {"cid": conversation_id},
            )
        )

    # -------------------------------- messages -------------------------------
    async def add_message(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        role: str,
        content: str,
        meta_json: str | None = None,
        message_id: str | None = None,
    ) -> str:
        mid = message_id or new_message_id()
        await self.execute(
            """
            INSERT INTO conversation_messages(message_id, turn_id, conversation_id,
                                              role, content, meta_json)
            VALUES (:mid, :tid, :cid, :role, :content, :meta)
            """,
            {"mid": mid, "tid": turn_id, "cid": conversation_id,
             "role": role, "content": content, "meta": meta_json},
        )
        return mid

    async def update_message(
        self, message_id: str, *, content: str, meta_json: str | None
    ) -> None:
        await self.execute(
            """
            UPDATE conversation_messages
            SET content = :content, meta_json = :meta
            WHERE message_id = :mid
            """,
            {"mid": message_id, "content": content, "meta": meta_json},
        )

    async def list_messages_for_conversation(
        self, conversation_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[dict]:
        sql = """
            SELECT m.message_id, m.turn_id, m.role, m.content, m.meta_json, m.created_at,
                   t.seq
            FROM conversation_messages m
            JOIN conversation_turns t ON t.turn_id = m.turn_id
            WHERE m.conversation_id = :cid
            ORDER BY t.seq, CASE m.role WHEN 'human' THEN 0 ELSE 1 END
        """
        params: dict = {"cid": conversation_id}
        if limit is not None:
            sql += " LIMIT :limit OFFSET :offset"
            params |= {"limit": limit, "offset": offset}
        return await self.fetch_all(sql, params)

    async def count_messages(self, conversation_id: str) -> int:
        return int(
            await self.scalar(
                "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = :cid",
                {"cid": conversation_id},
            )
        )

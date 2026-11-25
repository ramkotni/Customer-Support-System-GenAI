"""Idempotent DDL for the tables this API owns (conversations / chat transcript).

Why this lives here and not in ``_DB/schema.sql``:
    ``_DB/schema.sql`` is the canonical *commerce* schema and ``_DB/build.sh``
    drops and rebuilds the database from it. Conversations are application
    runtime state, not seed data, so the API owns them and recreates them on
    startup if they are missing. Everything below is ``CREATE ... IF NOT
    EXISTS``, so running it on every boot is safe and cheap.

Conventions follow the commerce schema (see ``_DB/README.md``):
    * Dates    : TEXT, ISO-8601 UTC via ``datetime('now')``.
    * Enums    : TEXT with CHECK constraints.
    * Tables   : STRICT (type-enforced).
    * FKs      : enforced — the engine sets ``PRAGMA foreign_keys = ON``.

Public ids are opaque strings (``conv_``/``turn_``/``msg_`` prefixes) rather
than sequential integers: they are handed to a browser, so they must not leak
volume or let one customer guess another's conversation.
"""
from __future__ import annotations

from sqlalchemy import text

from app.core.logging import get_logger
from app.db.session import get_sessionmaker

logger = get_logger("app.db.migrations")

# Note: SQLite requires one statement per execute() call.
_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS conversations (
        conversation_id TEXT    PRIMARY KEY,
        customer_id     INTEGER NOT NULL,
        title           TEXT    NOT NULL,
        status          TEXT    NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active','archived')),
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    ) STRICT
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversations_customer
        ON conversations(customer_id, updated_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_turns (
        turn_id         TEXT    PRIMARY KEY,
        conversation_id TEXT    NOT NULL,
        seq             INTEGER NOT NULL,
        status          TEXT    NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','complete','error')),
        error           TEXT,
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (conversation_id, seq),
        FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
            ON DELETE CASCADE
    ) STRICT
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_turns_conversation
        ON conversation_turns(conversation_id, seq)
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_messages (
        message_id      TEXT    PRIMARY KEY,
        turn_id         TEXT    NOT NULL,
        conversation_id TEXT    NOT NULL,
        role            TEXT    NOT NULL CHECK (role IN ('human','ai')),
        content         TEXT    NOT NULL,
        -- Agent metadata (tools called, KB citations, escalation) as a JSON
        -- blob. Phase 3 writes it; the UI renders it as chips.
        meta_json       TEXT,
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (turn_id) REFERENCES conversation_turns(turn_id)
            ON DELETE CASCADE,
        FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
            ON DELETE CASCADE
    ) STRICT
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_messages_turn
        ON conversation_messages(turn_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_messages_conversation
        ON conversation_messages(conversation_id, created_at)
    """,
)


async def ensure_chat_schema() -> None:
    """Create the conversation tables if they are not already present."""
    maker = get_sessionmaker()
    async with maker() as session:
        for statement in _STATEMENTS:
            await session.execute(text(statement))
        await session.commit()
    logger.info("chat_schema_ready", extra={"tables": 3})

"""Conversation / chat transcript schemas.

The shape mirrors the frontend's data model exactly:

    Conversation -> ordered turns[] -> { human: Message, ai: Message | None }

Field names are camelCase on the wire (via alias generator) because the client
is a TypeScript app, while the Python side stays snake_case. ``populate_by_name``
means either spelling is accepted on input.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

Role = Literal["human", "ai"]
TurnStatus = Literal["pending", "complete", "error"]


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def _non_blank(value: str | None) -> str | None:
    """Trim, and treat a whitespace-only string as absent.

    ``min_length`` alone would happily accept ``"   "`` as a question or a
    title, so every free-text field runs through this.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


# --------------------------------- outputs ---------------------------------

class MessageMetaOut(_CamelModel):
    """What the agent used to produce an answer. Phase 3 fills this in."""
    tools: list[str] = []
    citations: list[str] = []
    escalated: bool = False


class MessageOut(_CamelModel):
    id: str = Field(..., examples=["msg_1f3c9a2b4d5e"])
    role: Role
    content: str
    created_at: str = Field(..., examples=["2026-11-20 09:14:02"])
    meta: MessageMetaOut | None = None


class TurnOut(_CamelModel):
    """One exchange: a human message and the AI response to it."""
    id: str = Field(..., examples=["turn_7b2e10c4af93"])
    status: TurnStatus
    human: MessageOut
    ai: MessageOut | None = None
    error: str | None = None


class ConversationSummaryOut(_CamelModel):
    """Sidebar row — no transcript, so listing stays cheap."""
    id: str = Field(..., examples=["conv_4a91de02bb17"])
    title: str
    created_at: str
    updated_at: str
    turn_count: int


class ConversationOut(_CamelModel):
    """Full conversation, including the ordered transcript."""
    id: str
    title: str
    created_at: str
    updated_at: str
    turns: list[TurnOut] = []


# --------------------------------- inputs ----------------------------------

class ConversationCreate(_CamelModel):
    """Both fields optional: a conversation can be opened empty, or opened
    with its first question in a single round trip."""
    title: str | None = Field(None, max_length=200)
    first_message: str | None = Field(None, max_length=4000)

    _trim = field_validator("title", "first_message")(_non_blank)


class ConversationUpdate(_CamelModel):
    title: str = Field(..., min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def _require_title(cls, value: str) -> str:
        trimmed = _non_blank(value)
        if trimmed is None:
            raise ValueError("Title cannot be blank.")
        return trimmed


class MessageCreate(_CamelModel):
    """A question from the customer."""
    content: str = Field(..., min_length=1, max_length=4000,
                         examples=["Where is my order ORD-100001?"])

    @field_validator("content")
    @classmethod
    def _require_content(cls, value: str) -> str:
        trimmed = _non_blank(value)
        if trimmed is None:
            raise ValueError("A question cannot be blank.")
        return trimmed


# ------------------------- streaming (SSE) payloads -------------------------
# Declared as schemas so the contract is documented even though SSE frames are
# written by hand. Each frame is `event: <name>` + `data: <json>`.

class StreamStart(_CamelModel):
    conversation_id: str
    title: str
    turn_id: str
    human_message_id: str
    ai_message_id: str


class StreamDelta(_CamelModel):
    """An incremental chunk of the answer. Append to what you already have."""
    text: str


class StreamMeta(_CamelModel):
    meta: MessageMetaOut


class StreamDone(_CamelModel):
    turn_id: str
    status: TurnStatus
    ai_message_id: str


class StreamError(_CamelModel):
    code: str
    message: str
    request_id: str | None = None


def meta_out(raw: dict[str, Any] | None) -> MessageMetaOut | None:
    """Build metadata from a stored JSON blob, tolerating absent/partial data."""
    if not raw:
        return None
    return MessageMetaOut(
        tools=list(raw.get("tools") or []),
        citations=list(raw.get("citations") or []),
        escalated=bool(raw.get("escalated") or False),
    )

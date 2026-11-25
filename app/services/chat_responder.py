"""The reply generator behind the chat endpoints — a PLACEHOLDER for Phase 3.

=============================================================================
SCOPE NOTE — there is deliberately NO agent here.
No LLM call, no tool selection, no orchestration, no multi-step loop. This
module exists so the transport (persistence, SSE streaming, cancellation,
error handling) can be built and tested end to end *before* the agentic layer
lands. It answers from a static keyword table.
=============================================================================

Phase 3 replaces this with the real harness by implementing one method:

    class AgentResponder:
        async def respond(self, *, question, history, customer_id):
            async for chunk in harness.run(...):
                yield ResponseChunk(text=chunk.text)
            yield ResponseChunk(meta=ResponseMeta(tools=[...], citations=[...]))

Then point ``get_chat_responder`` in ``app/api/deps.py`` at it. Nothing in the
router, service, repository or frontend changes: they already handle an async
stream of text chunks followed by a metadata chunk.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Chunk size / delay for the simulated token stream. Small enough that the UI
# renders progressively, large enough not to flood the connection.
_WORDS_PER_CHUNK = 3
_CHUNK_DELAY_SECONDS = 0.02


@dataclass(frozen=True)
class ResponseMeta:
    """What the agent used. The stub fills it with plausible values."""
    tools: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    escalated: bool = False


@dataclass(frozen=True)
class ResponseChunk:
    """Either a slice of answer text, or the final metadata."""
    text: str | None = None
    meta: ResponseMeta | None = None


@dataclass(frozen=True)
class ContextMessage:
    """A prior message handed to the responder as conversation context."""
    role: str      # 'human' | 'ai'
    content: str
    message_id: str


@runtime_checkable
class ChatResponder(Protocol):
    """The contract the Phase 3 agent harness must satisfy."""

    def respond(
        self,
        *,
        question: str,
        history: list[ContextMessage],
        customer_id: int,
    ) -> AsyncIterator[ResponseChunk]:
        ...


# --------------------------------------------------------------------------
# Static answer table. Ported from the Phase 1 frontend mock so the UI keeps
# behaving identically once it is talking to the real backend.
# --------------------------------------------------------------------------

# Order matters: the first match wins, so specific signals come before broad
# ones ("damaged" before the tracking rule's `arriv\w*`, which also matches
# "arrived damaged").
_RULES: tuple[tuple[re.Pattern[str], str, ResponseMeta], ...] = (
    (
        re.compile(r"\b(damaged|broken|cracked|wrong item|defect\w*|missing)\b", re.I),
        """I'm sorry it arrived damaged — that's covered with no return shipment needed.

I've opened claim **CLM-2291** on order **#A-10428**. Your options:

- **Replacement** — ships today, arrives in 2–4 days.
- **Full refund** — $64.00 back to your Visa ending 4412.

Which would you prefer? Photos help us with the carrier but aren't required for \
you to be made whole.""",
        ResponseMeta(
            tools=["POST /orders/{id}/claims", "GET /orders/{id}/items"],
            citations=["_KB/damaged-or-wrong-items/damaged-on-arrival.md"],
        ),
    ),
    (
        re.compile(r"\b(where|wismo|track|tracking|shipped|shipment|arriv\w*)\b", re.I),
        """Your order **#A-10428** is in transit and on schedule.

- **Carrier:** Dispatch Express · tracking `DE4471980255`
- **Last scan:** Nov 18, 06:12 — departed Hyderabad sorting hub
- **Estimated delivery:** Nov 21 (by 8:00 PM)

Nothing is wrong with the shipment, so there is no action for you to take. \
If it has not arrived by the 21st, come back here and I can open a delay claim for you.""",
        ResponseMeta(
            tools=["GET /orders/{id}/tracking", "GET /orders/{id}/delivery-estimate"],
            citations=["_KB/shipping-and-delivery/tracking-a-shipment.md"],
        ),
    ),
    (
        re.compile(r"\b(refund|money back|reimburse\w*)\b", re.I),
        """I checked order **#A-10428** against the refund policy and it **is eligible**.

| Check | Result |
| --- | --- |
| Within 30-day window | Yes — delivered 9 days ago |
| Prior refund on this order | None |
| Payment dispute open | No |
| Amount | $64.00 — under my $100 limit |

I can issue the full **$64.00** back to the Visa ending **4412**, landing in 5–7 \
business days. Say **"yes, refund it"** and I'll process it.""",
        ResponseMeta(
            tools=["GET /orders/{id}/refund-eligibility", "GET /orders/{id}/payment"],
            citations=[
                "_KB/refunds/refund-eligibility-window.md",
                "_KB/refunds/agent-refund-limits.md",
            ],
        ),
    ),
    (
        re.compile(r"\b(return|exchange|send it back|rma)\b", re.I),
        """Two of the three items on order **#A-10428** can be returned.

1. **Aurora Desk Lamp** — returnable until **Dec 8**. Unused, original packaging.
2. **Cotton Throw Blanket** — returnable until **Dec 8**.
3. **Refill Cartridge (2-pack)** — *not* returnable; consumables are final sale once opened.

Return shipping is free and I'll email you a prepaid label. Which items should I set up?""",
        ResponseMeta(
            tools=["GET /orders/{id}/return-eligibility", "GET /orders/{id}/items"],
            citations=[
                "_KB/returns/what-can-be-returned.md",
                "_KB/product-specific-rules/consumables.md",
            ],
        ),
    ),
    (
        re.compile(r"\b(late|delay\w*|overdue|still not|hasn'?t (arrived|come))\b", re.I),
        """Order **#A-10428** is **4 days late** against its promised date of Nov 15.

Under the delay compensation policy, a 3–6 day delay qualifies for **$5 in store \
credit**, which I can apply right now — it never expires and stacks with promotions.

Want me to add the $5 credit while we wait on the carrier?""",
        ResponseMeta(
            tools=["GET /orders/{id}/delivery-estimate", "GET /orders/{id}/status"],
            citations=["_KB/delays-and-compensation/delay-credit-tiers.md"],
        ),
    ),
    (
        re.compile(r"\b(cancel\w*)\b", re.I),
        """Order **#A-10428** has already shipped, so it can no longer be cancelled.

What I *can* do instead:

- **Refuse at the door** — it returns automatically and refunds in full.
- **Accept it, then return it** — I'll pre-issue a free label now.

Shall I prepare the return label so it's waiting in your inbox?""",
        ResponseMeta(
            tools=["GET /orders/{id}/cancellation-eligibility", "GET /orders/{id}/status"],
            citations=["_KB/order-management/cancellation-cutoffs.md"],
        ),
    ),
    (
        re.compile(
            r"\b(human|agent|manager|supervisor|speak to someone|escalat\w*)\b", re.I
        ),
        """Of course — I've escalated this to a human specialist.

**Ticket #SUP-77310** · priority **High** · queue **Billing & Refunds**

You'll get an email reply within **4 business hours**. The full transcript of this \
conversation is attached, so you won't need to repeat anything.""",
        ResponseMeta(
            tools=["POST /tickets"],
            citations=["_KB/escalation/when-to-escalate.md"],
            escalated=True,
        ),
    ),
    (
        re.compile(r"\b(credit|promo|coupon|discount|gift card)\b", re.I),
        """Your store credit balance is **$12.50**.

- Applies automatically at checkout, before any promo code.
- **Never expires**, and it stacks with sale pricing and coupons.
- Not transferable, and it can't be cashed out.

Anything else I can check on your account?""",
        ResponseMeta(
            tools=["GET /customers/{id}/store-credit"],
            citations=["_KB/store-credit-and-promotions/using-store-credit.md"],
        ),
    ),
)

_FALLBACK = (
    """Happy to help with that.

The agent layer isn't wired up yet (that's Phase 3), so this answer comes from a \
**placeholder responder** on the backend. Here's what the real agent will do with a \
question like yours:

- Pull your order and its current status through the Tool APIs
- Check it against the relevant policy in the knowledge base
- Tell you the verdict, then act only after you confirm

Try asking about **tracking**, a **refund**, a **return**, a **late delivery**, a \
**damaged item**, or ask for a **human**.""",
    ResponseMeta(citations=["_KB/README.md"]),
)


def _lookup(question: str) -> tuple[str, ResponseMeta]:
    for pattern, answer, meta in _RULES:
        if pattern.search(question):
            return answer, meta
    return _FALLBACK


class StubChatResponder:
    """Streams a canned answer. See the module docstring — not an agent."""

    def __init__(self, chunk_delay: float = _CHUNK_DELAY_SECONDS) -> None:
        self._chunk_delay = chunk_delay

    async def respond(
        self,
        *,
        question: str,
        history: list[ContextMessage],
        customer_id: int,
    ) -> AsyncIterator[ResponseChunk]:
        # `history` and `customer_id` are accepted (and plumbed through from the
        # stored transcript) so Phase 3 has them without touching the callers.
        del history, customer_id

        answer, meta = _lookup(question)

        # Emit in small word groups so the client renders progressively.
        tokens = re.findall(r"\S+\s*", answer) or [answer]
        for start in range(0, len(tokens), _WORDS_PER_CHUNK):
            yield ResponseChunk(text="".join(tokens[start:start + _WORDS_PER_CHUNK]))
            if self._chunk_delay:
                await asyncio.sleep(self._chunk_delay)

        yield ResponseChunk(meta=meta)

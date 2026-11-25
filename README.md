# Customer Support API — Category 1: Tool APIs (FastAPI)

Production-grade, API-driven backend for the Agentic Customer Support System.
It serves two surfaces from one FastAPI app:

1. **Category 1 — the Tool APIs**: secure, mostly read-only REST endpoints over
   the commerce database that the support agent (Category 2, a later phase) will
   consume as tools. The agent never touches the database; it goes through
   exactly these endpoints.
2. **The conversation APIs**: the chat transcript the support UI reads and
   writes — conversations, turns, messages, and a streaming (SSE) answer
   endpoint. Answers currently come from a **placeholder responder**
   (`app/services/chat_responder.py`); the agent harness replaces it without
   changing the HTTP contract.

- **Knowledge base**: `_KB/` (82 Markdown policy articles) — built.
- **Commerce DB**: `_DB/ecommerce.db` (SQLite, 34 tables + 7 policy views) — built & seeded.
- **Backend**: the FastAPI layer in `app/`.
- **Frontend**: the React chat UI in `frontend/` — see `frontend/README.md`.

> The policy views in the DB (`v_refund_eligibility`, `v_delivery_status`, …)
> encode the business rules once; the API reads them so the API and the KB can
> never disagree (30-day window, one refund/order, $100 agent limit, delay
> tiers $0/$5/$15, dispute blocks).

---

## 1. Architecture

Layered, with strict separation of concerns:

```
HTTP request
  │
  ▼
Router (app/api/v1/routers/*)      HTTP only: validation, auth deps, status codes
  │
  ▼
Service (app/services/*)           business logic, policy enforcement, schema mapping
  │
  ▼
Repository (app/repositories/*)    all SQL (parameterized), nothing else
  │
  ▼
Async SQLAlchemy 2.0  →  SQLite (_DB/ecommerce.db)   [Postgres-portable]
```

Cross-cutting (in `app/core/`): environment config, structured JSON logging,
request **correlation IDs**, centralized exception handling (no internal
leakage), API-key **auth + customer-ownership**, in-memory **rate limiting**,
and pagination/sort helpers. Mutating endpoints support **idempotency**.

### Request/response contract
- Success: the resource (Pydantic v2 schema). Lists are wrapped in `{items, meta}`.
- Error: a consistent envelope `{"error": {code, message, details, request_id}}`.
- Money is integer **cents** everywhere, surfaced as `{cents, currency, display}`.

---

## 2. Endpoint catalog

Base prefix: `/api/v1`. All endpoints require `X-API-Key`. Customer/order
endpoints additionally require the caller to be authorized for that customer
(see Auth).

### Customers
| Method | Path | Purpose |
|---|---|---|
| GET | `/customers/{customer_id}` | Customer profile |
| GET | `/customers/{customer_id}/orders` | Customer's orders (paginated, filter/sort) |
| GET | `/customers/{customer_id}/addresses` | Saved addresses |
| GET | `/customers/{customer_id}/store-credit` | Store-credit balance |

### Orders — read-only
| Method | Path | Purpose |
|---|---|---|
| GET | `/orders/{order_id}` | Full order detail + items |
| GET | `/orders/{order_id}/status` | Current status |
| GET | `/orders/{order_id}/tracking` | Shipment + carrier timeline (WISMO) |
| GET | `/orders/{order_id}/items` | Line items |
| GET | `/orders/{order_id}/payment` | Payment records (brand + last4 only) |
| GET | `/orders/{order_id}/history` | Status history |
| GET | `/orders/{order_id}/invoice` | Invoice |
| GET | `/orders/{order_id}/delivery-estimate` | Delivery estimate / lateness |
| GET | `/orders/{order_id}/refund-eligibility` | **Verdict only — does not refund** |
| GET | `/orders/{order_id}/return-eligibility` | Per-item return rules |
| GET | `/orders/{order_id}/cancellation-eligibility` | Can it still be cancelled |
| GET | `/orders/{order_id}/refunds` | Existing refund records |
| GET | `/orders/{order_id}/returns` | Existing return records |
| GET | `/orders/{order_id}/claims` | Existing claim records |

### Orders — writes (POST, idempotent, policy-gated)
| Method | Path | Purpose |
|---|---|---|
| POST | `/orders/{order_id}/refunds` | Initiate refund (requires confirmation; **>$100 → human approval**) |
| POST | `/orders/{order_id}/returns` | Create a return (RMA) |
| POST | `/orders/{order_id}/claims` | Open a damaged/wrong/missing claim |
| POST | `/orders/{order_id}/cancel` | Cancel an unshipped order (requires confirmation) |
| POST | `/orders/{order_id}/goodwill-credit` | Grant delay goodwill ($0/$5/$15, max one/order) |

### Products & Tickets
| Method | Path | Purpose |
|---|---|---|
| GET | `/products` | Search/list catalog (paginated) |
| GET | `/products/{product_id}` | Product detail |
| POST | `/tickets` | Create a support ticket |
| GET | `/tickets/{ticket_number}` | Retrieve a ticket + messages |

Interactive docs: **`/docs`** (Swagger) and **`/redoc`**. Liveness: **`/health`**.

### Conversations (chat UI)
| Method | Path | Purpose |
|---|---|---|
| POST | `/conversations` | Open a conversation (optional `firstMessage` titles it) |
| GET | `/conversations` | Conversation history for the sidebar (paginated) |
| GET | `/conversations/{conversation_id}` | Full transcript (ordered turns) |
| PATCH | `/conversations/{conversation_id}` | Rename |
| DELETE | `/conversations/{conversation_id}` | Delete (turns + messages cascade) |
| GET | `/conversations/{conversation_id}/messages` | Flat message list (paginated) |
| POST | `/conversations/{conversation_id}/messages` | Ask; waits for the whole answer |
| POST | `/conversations/{conversation_id}/messages/stream` | Ask; **streams** the answer (SSE) |
| DELETE | `/conversations/{conversation_id}/turns/{turn_id}` | Drop a turn (powers the UI's Retry) |

A conversation is always owned by a customer, so these require `X-Customer-Id`
(422 `customer_scope_required` without it) and enforce ownership like the order
endpoints: 404 for a conversation that doesn't exist, 403 for someone else's.

Unlike the rest of the API, these responses are **camelCase**, because the only
consumer is the TypeScript frontend. Input accepts either spelling.

#### Data model
```
Conversation (conv_…)        title derived from the first question
  └─ Turn (turn_…)           pending | complete | error
       ├─ human Message (msg_…)
       └─ ai    Message (msg_…)   + meta: tools called, KB citations, escalated
```
Only `complete` turns become conversation context, so a failed or stopped
exchange never poisons the next question. The tables are owned by the API and
created at startup (`app/db/migrations.py`) rather than living in
`_DB/schema.sql`, which `_DB/build.sh` drops and rebuilds.

#### Streaming contract
`POST …/messages/stream` returns `text/event-stream`:

| Event | Payload | Notes |
|---|---|---|
| `start` | `{conversationId, title, turnId, humanMessageId, aiMessageId}` | ids are known before generation begins |
| `delta` | `{text}` | zero or more; append in order |
| `meta` | `{meta: {tools, citations, escalated}}` | once, before `done` |
| `done` | `{turnId, status, aiMessageId}` | terminal, on success |
| `error` | `{code, message, requestId}` | terminal, instead of `done` |

The turn is persisted whatever happens. If the client disconnects (the UI's
Stop button) or generation raises, the partial text is kept and the turn is
marked `error` with a reason, so Retry works and nothing is silently lost.

### Key business rules enforced
- **A refund question is never consent.** `POST .../refunds` requires
  `customer_confirmed=true`; otherwise `409 confirmation_required`.
- **$100 agent limit.** Order value ≤ $100 and in-window → agent-issued
  (`approved_by_type=ai_agent`). Over $100 → **not issued**; a `pending` refund +
  human **approval** + escalation **ticket** are created.
- **One refund per order / open dispute / outside window / not delivered** →
  the write is declined with a specific error code; `refund-eligibility` returns
  the matching verdict.
- **GETs are strictly read-only.** All mutations are POST and audited.

---

## 3. Auth & authorization

Send `X-API-Key` (configured in `API_KEYS`). To act for a specific customer,
also send `X-Customer-Id`. Ownership is then enforced on every customer/order
resource (fail-closed `403 ownership_violation` on mismatch). The future agent
will call these same endpoints with its own key, scoped per chat customer — so
it can never read another customer's data.

```bash
curl -s localhost:8000/api/v1/customers/1 \
  -H "X-API-Key: dev-public-key" -H "X-Customer-Id: 1" | jq
```

---

## 4. Setup & run

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), and the seeded DB
(`bash _DB/build.sh`, already present).

```bash
uv sync                           # creates .venv and installs deps from pyproject.toml
cp .env.example .env              # optional; sensible defaults otherwise

uv run uvicorn app.main:app --reload     # http://localhost:8000/docs
```

Dependencies live in `pyproject.toml` (`uv sync` installs the `dev` group too;
use `uv sync --no-dev` for a runtime-only env).

Environment (all optional; see `.env.example`): `DATABASE_URL`, `API_KEYS`,
`RATE_LIMIT_PER_MINUTE`, `LOG_JSON`, `AGENT_REFUND_APPROVAL_LIMIT_CENTS`.

### Example

```bash
export K="-H X-API-Key:dev-public-key -H X-Customer-Id:1"
curl -s $K localhost:8000/api/v1/customers/1/orders | jq '.items[0]'
# refund eligibility (read):
curl -s $K localhost:8000/api/v1/orders/1/refund-eligibility | jq
# issue a refund (write, needs confirmation + idempotency key):
curl -s -X POST $K -H "Idempotency-Key: r-1" \
  -d '{"reason":"changed my mind","customer_confirmed":true}' \
  -H "Content-Type: application/json" \
  localhost:8000/api/v1/orders/1/refunds | jq
```

---

## 5. Tests

```bash
uv run pytest    # 36 tests: auth, ownership, pagination, all read paths,
                 # refund verdicts, confirmation/$100/idempotency, returns,
                 # cancellation, goodwill, claims, tickets
```

Each test runs against a fresh copy of `_DB/ecommerce.db`, asserting against the
seeded eval fixtures `ORD-100001..100011`.

---

## 6. Docker

```bash
docker compose up --build        # http://localhost:8000/docs
```

Runs as a non-root user with a `/health` healthcheck.

---

## 7. Portability (SQLite → PostgreSQL)

The code is DB-agnostic above the engine. Set
`DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db` and add `asyncpg` to
`requirements.txt`. The schema (`_DB/schema.sql`) is written to port cleanly
(see `_DB/README.md`); the FK `PRAGMA` is SQLite-only and auto-skipped.

---

## 8. Project structure

```
app/
  core/        config, logging, correlation, security, exceptions, rate_limit, pagination
  db/          async engine/session (FK pragma per connection) + chat-table DDL
  schemas/     Pydantic v2 request/response models
  repositories/ parameterized SQL only (SQLAlchemy 2.0 async)
  services/    business logic + policy enforcement + idempotency
               chat_responder.py  <- PLACEHOLDER the agent replaces
               chat_stream.py     <- SSE transport for a chat turn
  api/         deps (DI, auth, ownership) + v1 routers
  main.py      app factory (middleware, exception handlers, routers)
frontend/      React + TypeScript chat UI (see frontend/README.md)
tests/         pytest (ASGI transport, per-test DB copy)
Dockerfile, docker-compose.yml, pyproject.toml, .env.example
```

## 9. Roadmap — Category 2 (next phase)
The agent orchestration layer (LangGraph, KB retrieval, tool calling, execution
observability) consumes the Tool APIs above. **Not included here.**

It does not need new HTTP endpoints: the chat endpoints already exist and are
wired to the UI. Swapping in the agent is a one-line change —
`get_chat_responder` in `app/api/deps.py` currently returns
`StubChatResponder`; return the harness instead. The contract it must satisfy
is one method:

```python
async def respond(self, *, question: str, history: list[ContextMessage],
                  customer_id: int) -> AsyncIterator[ResponseChunk]:
    ...   # yield ResponseChunk(text=...) repeatedly, then ResponseChunk(meta=...)
```

Prior turns are already assembled and passed in as `history`; persistence,
streaming, cancellation and error handling are already built and tested around
it. See the module docstring in `app/services/chat_responder.py`.

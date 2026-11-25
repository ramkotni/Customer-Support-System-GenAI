# Frontend — React chat UI

ChatGPT-style support chat for the Agentic Customer Support System. It talks to
the FastAPI backend in `../app`: conversations, turns and messages are **stored
server-side**, and answers **stream in over SSE**.

React 18 + TypeScript + Vite. Plain CSS, no UI framework, no state library —
one reducer and one hook.

## Run it

The backend must be running first:

```bash
# terminal 1 — API on :8000
cd ..
uv run uvicorn app.main:app --reload

# terminal 2 — UI on :5173
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` to `127.0.0.1:8000`, so there
is no CORS setup in development.

```bash
npm run build      # typecheck + production bundle into dist/
npm run typecheck
```

### Configuration

Defaults work out of the box. To change them, copy `.env.example` to
`.env.local`:

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE` | `/api/v1` | Relative, so it goes through the dev proxy |
| `VITE_API_KEY` | `dev-public-key` | Sent as `X-API-Key` |
| `VITE_CUSTOMER_ID` | `1` | Sent as `X-Customer-Id` |

There is no sign-in screen in this phase: the customer id is configuration.
The backend scopes every conversation to it and rejects access to anyone
else's, so a real login slots in at `src/api/config.ts`.

## What's implemented

| Requirement | Where |
|---|---|
| Sidebar history, grouped by recency, from `GET /conversations` | `src/components/Sidebar.tsx` |
| Prompt bar (Enter sends, Shift+Enter newline, auto-grow, Stop) | `src/components/PromptBar.tsx` |
| Multi-turn chat window, follows the stream unless you scroll up | `src/components/ChatWindow.tsx` |
| Streamed answers over SSE | `src/api/sse.ts`, `src/api/conversations.ts` |
| Server-side conversation / turn / message ids | the backend; see `src/types.ts` |
| Per-turn error + Retry (drops the turn server-side, re-asks) | `src/state/useChat.ts` |

Also: new/select/rename (double-click a sidebar item)/delete conversation,
light & dark themes, mobile layout, markdown answers (tables, lists, bold,
inline code) rendered to React elements rather than `innerHTML`.

Out of scope by design: **document/file uploads**.

## Architecture

```
components/     presentational; no data fetching
state/
  reducer.ts    pure state transitions
  useChat.ts    orchestration: fetch, stream, optimistic turn, reconcile
api/
  config.ts     base URL + auth headers
  client.ts     fetch wrapper; parses the error envelope into ApiError
  sse.ts        SSE frame reader for fetch response bodies
  conversations.ts  typed endpoint calls + streamAnswer()
lib/            markdown renderer, date formatting
```

The server owns the transcript. A question is appended optimistically so typing
feels instant, then the `start` frame swaps in the real ids, and once the turn
settles the conversation is refetched so stored ids, timestamps and any partial
text are authoritative.

Two details worth knowing if you touch this:

- **`EventSource` is not used.** It cannot issue a POST or send auth headers, so
  the stream is read off a `fetch` response body (`src/api/sse.ts`). That parser
  handles frames split across arbitrary network chunk boundaries, including
  multi-byte UTF-8 split mid-character.
- **Stop must reject, not resolve.** An aborted stream throws `AbortError` so
  the UI can tell "stopped" from "finished"; otherwise a cancelled turn would
  briefly render as complete. The backend independently records the turn as
  `error` with the partial answer kept, which is what Retry builds on.

## Phase 3 (the agent)

Nothing here changes. The UI already sends each question to
`POST /conversations/{id}/messages/stream` and renders `delta` frames plus the
tool/citation chips from the `meta` frame. The backend swaps its placeholder
responder for the agent harness — see the Roadmap in `../README.md`.

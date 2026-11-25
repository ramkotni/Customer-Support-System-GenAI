import type { Conversation, ConversationSummary, Message, Turn } from '../types';
import { openEventStream, request } from './client';
import { readEventStream } from './sse';

/* ------------------------------ wire types ------------------------------- */
// The API serialises camelCase, so these line up with the app's own model.

interface Page<T> {
  items: T[];
  meta: { total: number; limit: number; offset: number; returned: number };
}

interface WireMessage {
  id: string;
  role: 'human' | 'ai';
  content: string;
  createdAt: string;
  meta?: { tools: string[]; citations: string[]; escalated: boolean } | null;
}

interface WireTurn {
  id: string;
  status: 'pending' | 'complete' | 'error';
  human: WireMessage;
  ai?: WireMessage | null;
  error?: string | null;
}

interface WireConversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  turns?: WireTurn[];
}

interface WireSummary {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  turnCount: number;
}

/* ------------------------------- adapters -------------------------------- */

function toMessage(w: WireMessage): Message {
  return {
    id: w.id,
    role: w.role,
    content: w.content,
    createdAt: w.createdAt,
    meta: w.meta
      ? {
          tools: w.meta.tools ?? [],
          citations: w.meta.citations ?? [],
          escalated: w.meta.escalated ?? false,
        }
      : undefined,
  };
}

function toTurn(w: WireTurn): Turn {
  return {
    id: w.id,
    status: w.status,
    human: toMessage(w.human),
    ai: w.ai ? toMessage(w.ai) : null,
    error: w.error ?? undefined,
  };
}

function toConversation(w: WireConversation): Conversation {
  return {
    id: w.id,
    title: w.title,
    createdAt: w.createdAt,
    updatedAt: w.updatedAt,
    turns: (w.turns ?? []).map(toTurn),
  };
}

function toSummary(w: WireSummary): ConversationSummary {
  return {
    id: w.id,
    title: w.title,
    createdAt: w.createdAt,
    updatedAt: w.updatedAt,
    turnCount: w.turnCount,
  };
}

/* ----------------------------- transcript -------------------------------- */

export async function listConversations(limit = 100): Promise<ConversationSummary[]> {
  const page = await request<Page<WireSummary>>(`/conversations?limit=${limit}&sort=updated_at`);
  return page.items.map(toSummary);
}

export async function getConversation(id: string): Promise<Conversation> {
  return toConversation(await request<WireConversation>(`/conversations/${id}`));
}

/** Open a conversation. Passing the first question lets the server title it. */
export async function createConversation(firstMessage?: string): Promise<Conversation> {
  return toConversation(
    await request<WireConversation>('/conversations', {
      method: 'POST',
      body: firstMessage ? { firstMessage } : {},
    }),
  );
}

export async function renameConversation(id: string, title: string): Promise<Conversation> {
  return toConversation(
    await request<WireConversation>(`/conversations/${id}`, {
      method: 'PATCH',
      body: { title },
    }),
  );
}

export async function deleteConversation(id: string): Promise<void> {
  await request<void>(`/conversations/${id}`, { method: 'DELETE' });
}

export async function deleteTurn(conversationId: string, turnId: string): Promise<void> {
  await request<void>(`/conversations/${conversationId}/turns/${turnId}`, { method: 'DELETE' });
}

/* ------------------------------- streaming -------------------------------- */

export interface StartEvent {
  conversationId: string;
  title: string;
  turnId: string;
  humanMessageId: string;
  aiMessageId: string;
}

export interface StreamMetaPayload {
  tools: string[];
  citations: string[];
  escalated: boolean;
}

/** One normalised step of an in-flight answer. */
export type AnswerEvent =
  | { type: 'start'; start: StartEvent }
  | { type: 'delta'; text: string }
  | { type: 'meta'; meta: StreamMetaPayload }
  | { type: 'done'; turnId: string }
  | { type: 'error'; code: string; message: string };

/**
 * Ask a question and stream the answer.
 *
 * This is the function the Phase 1 mock (`mock/agentClient.ts`) stood in for.
 * It now talks to `POST /conversations/{id}/messages/stream` and translates SSE
 * frames into the events the UI consumes.
 */
export async function* streamAnswer(
  conversationId: string,
  question: string,
  signal?: AbortSignal,
): AsyncGenerator<AnswerEvent> {
  const response = await openEventStream(
    `/conversations/${conversationId}/messages/stream`,
    { content: question },
    signal,
  );

  for await (const frame of readEventStream(response, signal)) {
    switch (frame.event) {
      case 'start':
        yield { type: 'start', start: frame.data as StartEvent };
        break;
      case 'delta':
        yield { type: 'delta', text: (frame.data as { text: string }).text };
        break;
      case 'meta':
        yield { type: 'meta', meta: (frame.data as { meta: StreamMetaPayload }).meta };
        break;
      case 'done':
        yield { type: 'done', turnId: (frame.data as { turnId: string }).turnId };
        break;
      case 'error': {
        const data = frame.data as { code: string; message: string };
        yield { type: 'error', code: data.code, message: data.message };
        break;
      }
      default:
        break; // forward-compatible: ignore unknown event names
    }
  }
}

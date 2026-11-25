/**
 * Core conversation data model — mirrors the backend contract in
 * `app/schemas/conversation.py`:
 *
 *   Conversation -> ordered Turn[] -> { human: Message, ai: Message | null }
 *
 * All ids are minted by the server (`conv_`/`turn_`/`msg_` prefixes) so a turn
 * can be referenced, retried or cited later (tool traces, tickets, feedback).
 */

export type Role = 'human' | 'ai';

export interface Message {
  /** Unique Message ID, e.g. "msg_9f2c1a4b". */
  id: string;
  role: Role;
  content: string;
  /** ISO-8601 UTC timestamp. */
  createdAt: string;
  /** Populated by the agent later (tool calls, KB citations, confidence...). */
  meta?: MessageMeta;
}

export interface MessageMeta {
  /** Names of tool APIs the agent called to produce this answer. */
  tools?: string[];
  /** KB article paths the answer was grounded in. */
  citations?: string[];
  /** True when the agent wants a human to take over. */
  escalated?: boolean;
}

export type TurnStatus = 'pending' | 'complete' | 'error';

export interface Turn {
  /** Unique Turn ID, e.g. "turn_3b81de02". */
  id: string;
  human: Message;
  /** null while the agent is still answering, or when the turn errored. */
  ai: Message | null;
  status: TurnStatus;
  /** Human-readable reason when status === 'error'. */
  error?: string;
}

export interface Conversation {
  /** Server-assigned Conversation ID, e.g. "conv_7d4e0f11a2b3". */
  id: string;
  /** Derived server-side from the user's first question. */
  title: string;
  createdAt: string;
  updatedAt: string;
  /** Ordered oldest -> newest. */
  turns: Turn[];
}

/** Sidebar row: no transcript, so listing stays cheap. */
export interface ConversationSummary {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  turnCount: number;
}

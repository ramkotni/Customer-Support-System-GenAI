import type { Conversation, ConversationSummary, Message, Turn } from '../types';
import type { StartEvent, StreamMetaPayload } from '../api/conversations';

/**
 * Client-side view of server state.
 *
 * `summaries` drives the sidebar; `loaded` caches full transcripts by id so
 * revisiting a conversation doesn't refetch. The server is the source of
 * truth — every action here reflects a call that already succeeded, except
 * the optimistic turn appended when a question is sent.
 */
export interface ChatState {
  summaries: ConversationSummary[];
  loaded: Record<string, Conversation>;
  /** null = the "new conversation" screen. */
  activeId: string | null;
  loadingList: boolean;
  loadingConversation: boolean;
  /** Set when the conversation list can't be reached at all. */
  listError: string | null;
}

/** The optimistic turn id, before the server tells us the real one. */
export const PENDING_TURN_ID = 'turn_pending';

export type Action =
  | { type: 'listLoading' }
  | { type: 'listLoaded'; summaries: ConversationSummary[] }
  | { type: 'listFailed'; message: string }
  | { type: 'conversationLoading' }
  | { type: 'conversationLoaded'; conversation: Conversation }
  | { type: 'newConversation' }
  | { type: 'selectConversation'; id: string }
  | { type: 'conversationCreated'; conversation: Conversation }
  | { type: 'conversationRemoved'; id: string }
  | { type: 'conversationRenamed'; id: string; title: string }
  /** Optimistically show the question while the answer streams. */
  | { type: 'questionSent'; conversationId: string; question: string }
  | { type: 'turnStarted'; conversationId: string; start: StartEvent }
  | { type: 'answerDelta'; conversationId: string; turnId: string; text: string }
  | { type: 'answerMeta'; conversationId: string; turnId: string; meta: StreamMetaPayload }
  | { type: 'turnCompleted'; conversationId: string; turnId: string }
  | { type: 'turnFailed'; conversationId: string; turnId: string; message: string }
  | { type: 'turnRemoved'; conversationId: string; turnId: string };

export const initialState: ChatState = {
  summaries: [],
  loaded: {},
  activeId: null,
  loadingList: true,
  loadingConversation: false,
  listError: null,
};

export function reducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case 'listLoading':
      return { ...state, loadingList: true, listError: null };

    case 'listLoaded':
      return { ...state, summaries: action.summaries, loadingList: false, listError: null };

    case 'listFailed':
      return { ...state, loadingList: false, listError: action.message };

    case 'conversationLoading':
      return { ...state, loadingConversation: true };

    case 'conversationLoaded':
      return {
        ...state,
        loadingConversation: false,
        loaded: { ...state.loaded, [action.conversation.id]: action.conversation },
        summaries: upsertSummary(state.summaries, action.conversation),
      };

    case 'newConversation':
      return { ...state, activeId: null };

    case 'selectConversation':
      return { ...state, activeId: action.id };

    case 'conversationCreated':
      return {
        ...state,
        activeId: action.conversation.id,
        loaded: { ...state.loaded, [action.conversation.id]: action.conversation },
        summaries: upsertSummary(state.summaries, action.conversation),
      };

    case 'conversationRemoved': {
      const loaded = { ...state.loaded };
      delete loaded[action.id];
      return {
        ...state,
        loaded,
        summaries: state.summaries.filter((s) => s.id !== action.id),
        activeId: state.activeId === action.id ? null : state.activeId,
      };
    }

    case 'conversationRenamed':
      return {
        ...state,
        summaries: state.summaries.map((s) =>
          s.id === action.id ? { ...s, title: action.title } : s,
        ),
        loaded: mapConversation(state.loaded, action.id, (c) => ({ ...c, title: action.title })),
      };

    case 'questionSent': {
      const now = new Date().toISOString();
      const turn: Turn = {
        id: PENDING_TURN_ID,
        status: 'pending',
        human: { id: 'msg_pending', role: 'human', content: action.question, createdAt: now },
        ai: null,
      };
      return {
        ...state,
        loaded: mapConversation(state.loaded, action.conversationId, (c) => ({
          ...c,
          updatedAt: now,
          turns: [...c.turns, turn],
        })),
        summaries: touchSummary(state.summaries, action.conversationId, now),
      };
    }

    case 'turnStarted': {
      // Replace the optimistic turn with the server's real ids.
      const { start } = action;
      return {
        ...state,
        loaded: mapConversation(state.loaded, action.conversationId, (c) => ({
          ...c,
          title: start.title || c.title,
          turns: c.turns.map((t) =>
            t.id === PENDING_TURN_ID
              ? {
                  ...t,
                  id: start.turnId,
                  human: { ...t.human, id: start.humanMessageId },
                }
              : t,
          ),
        })),
        summaries: state.summaries.map((s) =>
          s.id === action.conversationId && start.title ? { ...s, title: start.title } : s,
        ),
      };
    }

    case 'answerDelta':
      return mapTurn(state, action.conversationId, action.turnId, (turn) => ({
        ...turn,
        ai: turn.ai
          ? { ...turn.ai, content: turn.ai.content + action.text }
          : blankAi(action.text),
      }));

    case 'answerMeta':
      return mapTurn(state, action.conversationId, action.turnId, (turn) => ({
        ...turn,
        ai: turn.ai
          ? { ...turn.ai, meta: action.meta }
          : { ...blankAi(''), meta: action.meta },
      }));

    case 'turnCompleted':
      return mapTurn(state, action.conversationId, action.turnId, (turn) => ({
        ...turn,
        status: 'complete',
        error: undefined,
      }));

    case 'turnFailed':
      return mapTurn(state, action.conversationId, action.turnId, (turn) => ({
        ...turn,
        status: 'error',
        error: action.message,
      }));

    case 'turnRemoved':
      return {
        ...state,
        loaded: mapConversation(state.loaded, action.conversationId, (c) => ({
          ...c,
          turns: c.turns.filter((t) => t.id !== action.turnId),
        })),
      };

    default:
      return state;
  }
}

/* --------------------------------- helpers -------------------------------- */

function blankAi(content: string): Message {
  return { id: 'msg_streaming', role: 'ai', content, createdAt: new Date().toISOString() };
}

function mapConversation(
  loaded: Record<string, Conversation>,
  id: string,
  fn: (c: Conversation) => Conversation,
): Record<string, Conversation> {
  const existing = loaded[id];
  if (!existing) return loaded;
  return { ...loaded, [id]: fn(existing) };
}

function mapTurn(
  state: ChatState,
  conversationId: string,
  turnId: string,
  fn: (turn: Turn) => Turn,
): ChatState {
  return {
    ...state,
    loaded: mapConversation(state.loaded, conversationId, (c) => ({
      ...c,
      // A turn still mid-handshake is addressed by its optimistic id.
      turns: c.turns.map((t) => (t.id === turnId || t.id === PENDING_TURN_ID ? fn(t) : t)),
    })),
  };
}

function upsertSummary(
  summaries: ConversationSummary[],
  conversation: Conversation,
): ConversationSummary[] {
  const summary: ConversationSummary = {
    id: conversation.id,
    title: conversation.title,
    createdAt: conversation.createdAt,
    updatedAt: conversation.updatedAt,
    turnCount: conversation.turns.length,
  };
  const index = summaries.findIndex((s) => s.id === conversation.id);
  if (index === -1) return [summary, ...summaries];
  const next = [...summaries];
  next[index] = { ...next[index], ...summary };
  return next;
}

function touchSummary(
  summaries: ConversationSummary[],
  id: string,
  updatedAt: string,
): ConversationSummary[] {
  return summaries.map((s) => (s.id === id ? { ...s, updatedAt } : s));
}

export const activeConversation = (state: ChatState): Conversation | undefined =>
  state.activeId ? state.loaded[state.activeId] : undefined;

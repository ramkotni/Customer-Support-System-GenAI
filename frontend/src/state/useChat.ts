import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import * as api from '../api/conversations';
import type { Conversation, ConversationSummary } from '../types';
import { PENDING_TURN_ID, activeConversation, initialState, reducer } from './reducer';

export interface UseChat {
  conversations: ConversationSummary[];
  activeConversation: Conversation | undefined;
  activeId: string | null;
  isStreaming: boolean;
  loadingList: boolean;
  loadingConversation: boolean;
  /** The conversation list could not be loaded (backend down, bad key…). */
  listError: string | null;
  send: (question: string) => void;
  stop: () => void;
  retry: (turnId: string) => void;
  newConversation: () => void;
  selectConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) return 'Could not reach the server. Is the API running?';
  return 'Something went wrong.';
}

export function useChat(): UseChat {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [isStreaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  /** Guards against a late response overwriting a newer selection. */
  const requestedIdRef = useRef<string | null>(null);

  /* ------------------------------ initial load ----------------------------- */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      dispatch({ type: 'listLoading' });
      try {
        const summaries = await api.listConversations();
        if (!cancelled) dispatch({ type: 'listLoaded', summaries });
      } catch (error) {
        if (!cancelled) dispatch({ type: 'listFailed', message: describe(error) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  /* --------------------------- transcript loading -------------------------- */
  const loadConversation = useCallback(async (id: string) => {
    requestedIdRef.current = id;
    dispatch({ type: 'conversationLoading' });
    try {
      const conversation = await api.getConversation(id);
      if (requestedIdRef.current === id) {
        dispatch({ type: 'conversationLoaded', conversation });
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        dispatch({ type: 'conversationRemoved', id });
        return;
      }
      dispatch({ type: 'listFailed', message: describe(error) });
    }
  }, []);

  const selectConversation = useCallback(
    (id: string) => {
      abortRef.current?.abort();
      dispatch({ type: 'selectConversation', id });
      // Always refetch: another turn may have been added elsewhere.
      void loadConversation(id);
    },
    [loadConversation],
  );

  /* -------------------------------- asking -------------------------------- */
  const runStream = useCallback(
    async (conversationId: string, question: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);

      // Until `start` arrives, the turn is addressed by its optimistic id.
      let turnId = PENDING_TURN_ID;
      let failed = false;

      try {
        for await (const event of api.streamAnswer(conversationId, question, controller.signal)) {
          switch (event.type) {
            case 'start':
              turnId = event.start.turnId;
              dispatch({ type: 'turnStarted', conversationId, start: event.start });
              break;
            case 'delta':
              dispatch({ type: 'answerDelta', conversationId, turnId, text: event.text });
              break;
            case 'meta':
              dispatch({ type: 'answerMeta', conversationId, turnId, meta: event.meta });
              break;
            case 'done':
              dispatch({ type: 'turnCompleted', conversationId, turnId: event.turnId });
              break;
            case 'error':
              failed = true;
              dispatch({ type: 'turnFailed', conversationId, turnId, message: event.message });
              break;
          }
        }
        if (!failed) {
          dispatch({ type: 'turnCompleted', conversationId, turnId });
        }
      } catch (error) {
        const aborted =
          controller.signal.aborted ||
          (error instanceof DOMException && error.name === 'AbortError');
        dispatch({
          type: 'turnFailed',
          conversationId,
          turnId,
          message: aborted ? 'Stopped.' : describe(error),
        });
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
          setStreaming(false);
        }
        // The server owns the transcript; reconcile once the turn settles so
        // real message ids, timestamps and any partial text are authoritative.
        void loadConversation(conversationId);
      }
    },
    [loadConversation],
  );

  const send = useCallback(
    (raw: string) => {
      const question = raw.trim();
      if (!question || isStreaming) return;

      (async () => {
        let conversationId = state.activeId;

        if (!conversationId) {
          // First question: the server creates the conversation and titles it.
          try {
            const created = await api.createConversation(question);
            dispatch({ type: 'conversationCreated', conversation: created });
            conversationId = created.id;
          } catch (error) {
            dispatch({ type: 'listFailed', message: describe(error) });
            return;
          }
        }

        dispatch({ type: 'questionSent', conversationId, question });
        await runStream(conversationId, question);
      })();
    },
    [isStreaming, runStream, state.activeId],
  );

  const retry = useCallback(
    (turnId: string) => {
      const conversation = activeConversation(state);
      if (!conversation || isStreaming) return;
      const turn = conversation.turns.find((t) => t.id === turnId);
      if (!turn) return;
      const question = turn.human.content;

      (async () => {
        // Drop the failed exchange server-side so it never becomes context.
        if (turnId !== PENDING_TURN_ID) {
          try {
            await api.deleteTurn(conversation.id, turnId);
          } catch (error) {
            if (!(error instanceof ApiError && error.status === 404)) {
              dispatch({ type: 'turnFailed', conversationId: conversation.id, turnId,
                         message: describe(error) });
              return;
            }
          }
        }
        dispatch({ type: 'turnRemoved', conversationId: conversation.id, turnId });
        dispatch({ type: 'questionSent', conversationId: conversation.id, question });
        await runStream(conversation.id, question);
      })();
    },
    [isStreaming, runStream, state],
  );

  /* ------------------------------- management ------------------------------ */
  const deleteConversation = useCallback((id: string) => {
    abortRef.current?.abort();
    dispatch({ type: 'conversationRemoved', id });
    void api.deleteConversation(id).catch(() => {
      // Deleting something already gone is fine; anything else resyncs below.
      void api.listConversations().then(
        (summaries) => dispatch({ type: 'listLoaded', summaries }),
        () => undefined,
      );
    });
  }, []);

  const renameConversation = useCallback((id: string, title: string) => {
    const trimmed = title.trim();
    if (!trimmed) return;
    dispatch({ type: 'conversationRenamed', id, title: trimmed });
    void api.renameConversation(id, trimmed).catch(() => {
      void api.listConversations().then(
        (summaries) => dispatch({ type: 'listLoaded', summaries }),
        () => undefined,
      );
    });
  }, []);

  const sorted = useMemo(
    () =>
      [...state.summaries].sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt)),
    [state.summaries],
  );

  return {
    conversations: sorted,
    activeConversation: activeConversation(state),
    activeId: state.activeId,
    isStreaming,
    loadingList: state.loadingList,
    loadingConversation: state.loadingConversation && !activeConversation(state),
    listError: state.listError,
    send,
    stop: useCallback(() => abortRef.current?.abort(), []),
    retry,
    newConversation: useCallback(() => {
      abortRef.current?.abort();
      dispatch({ type: 'newConversation' });
    }, []),
    selectConversation,
    deleteConversation,
    renameConversation,
  };
}

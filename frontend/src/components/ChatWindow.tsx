import { useEffect, useRef } from 'react';
import type { Conversation } from '../types';
import { TurnView } from './TurnView';
import { EmptyState } from './EmptyState';

interface Props {
  conversation: Conversation | undefined;
  isStreaming: boolean;
  /** A transcript is being fetched and there is nothing to show yet. */
  loading: boolean;
  onRetry: (turnId: string) => void;
  onPickSuggestion: (text: string) => void;
}

export function ChatWindow({
  conversation,
  isStreaming,
  loading,
  onRetry,
  onPickSuggestion,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  // Follow the stream, but stop following once the user scrolls up.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const lastTurn = conversation?.turns.at(-1);
  const tick = `${conversation?.id ?? ''}:${conversation?.turns.length ?? 0}:${lastTurn?.ai?.content.length ?? 0}`;

  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [tick]);

  // A newly opened conversation starts at the bottom.
  useEffect(() => {
    pinnedRef.current = true;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [conversation?.id]);

  if (!conversation) {
    return (
      <div className="chat" ref={scrollRef}>
        {loading ? (
          <p className="chat__loading">Loading conversation…</p>
        ) : (
          <EmptyState onPick={onPickSuggestion} />
        )}
      </div>
    );
  }

  return (
    <div className="chat" ref={scrollRef}>
      <div className="chat__inner">
        {conversation.turns.map((turn) => (
          <TurnView
            key={turn.id}
            turn={turn}
            isStreaming={isStreaming && turn.id === lastTurn?.id}
            onRetry={onRetry}
          />
        ))}
      </div>
    </div>
  );
}

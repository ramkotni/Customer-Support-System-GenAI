import { useEffect, useRef, useState } from 'react';
import { SendIcon, StopIcon } from './Icons';

interface Props {
  onSend: (text: string) => void;
  onStop: () => void;
  isStreaming: boolean;
  /** Re-focus when the active conversation changes. */
  focusKey: string;
}

const MAX_ROWS_PX = 200;

export function PromptBar({ onSend, onStop, isStreaming, focusKey }: Props) {
  const [value, setValue] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with content, up to a cap.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_ROWS_PX)}px`;
  }, [value]);

  useEffect(() => {
    ref.current?.focus();
  }, [focusKey]);

  function submit() {
    if (!value.trim() || isStreaming) return;
    onSend(value);
    setValue('');
  }

  return (
    <div className="promptbar">
      <form
        className="promptbar__form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          ref={ref}
          className="promptbar__input"
          rows={1}
          value={value}
          placeholder="Ask about an order, refund, return or delivery…"
          aria-label="Your question"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter makes a new line.
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />

        {isStreaming ? (
          <button className="promptbar__btn promptbar__btn--stop" type="button" onClick={onStop} aria-label="Stop generating">
            <StopIcon />
          </button>
        ) : (
          <button
            className="promptbar__btn"
            type="submit"
            disabled={!value.trim()}
            aria-label="Send question"
          >
            <SendIcon />
          </button>
        )}
      </form>

      <p className="promptbar__hint">
        Enter to send · Shift+Enter for a new line · answers come from a placeholder
        responder until the agent ships
      </p>
    </div>
  );
}

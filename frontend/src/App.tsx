import { useState } from 'react';
import { ChatWindow } from './components/ChatWindow';
import { PromptBar } from './components/PromptBar';
import { Sidebar } from './components/Sidebar';
import { SidebarIcon } from './components/Icons';
import { useChat } from './state/useChat';

export default function App() {
  const chat = useChat();
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const turnCount = chat.activeConversation?.turns.length ?? 0;

  return (
    <div className={`app${sidebarOpen ? '' : ' app--narrow'}`}>
      <Sidebar
        conversations={chat.conversations}
        activeId={chat.activeId}
        open={sidebarOpen}
        loading={chat.loadingList}
        error={chat.listError}
        onNew={chat.newConversation}
        onSelect={chat.selectConversation}
        onDelete={chat.deleteConversation}
        onRename={chat.renameConversation}
      />

      <main className="main">
        <header className="topbar">
          <button
            className="icon-btn"
            type="button"
            onClick={() => setSidebarOpen((v) => !v)}
            aria-label={sidebarOpen ? 'Hide conversation history' : 'Show conversation history'}
            aria-expanded={sidebarOpen}
          >
            <SidebarIcon />
          </button>

          <div className="topbar__titles">
            <h1 className="topbar__title">
              {chat.activeConversation?.title ?? 'Support Assistant'}
            </h1>
            {chat.activeConversation && (
              <p className="topbar__sub">
                {chat.activeConversation.id} · {turnCount} turn{turnCount === 1 ? '' : 's'}
              </p>
            )}
          </div>
        </header>

        <ChatWindow
          conversation={chat.activeConversation}
          isStreaming={chat.isStreaming}
          loading={chat.loadingConversation}
          onRetry={chat.retry}
          onPickSuggestion={chat.send}
        />

        <PromptBar
          onSend={chat.send}
          onStop={chat.stop}
          isStreaming={chat.isStreaming}
          focusKey={chat.activeId ?? 'new'}
        />
      </main>
    </div>
  );
}

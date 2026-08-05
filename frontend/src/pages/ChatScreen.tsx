import { useEffect, useRef, useState } from "react";
import { api } from "../api";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
}

const EXAMPLE_QUESTIONS = [
  "What do I currently hold?",
  "What needs my attention right now?",
];

export function ChatScreen() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [progressLog, setProgressLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const closeStreamRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, progressLog]);

  useEffect(() => () => closeStreamRef.current?.(), []); // close the stream if the page unmounts mid-answer

  function ask(question: string) {
    if (!question.trim() || sending) return;
    closeStreamRef.current?.();
    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setInput("");
    setError(null);
    setSending(true);
    setProgressLog([]);
    closeStreamRef.current = api.streamChat(
      question,
      (message) => setProgressLog((prev) => [...prev, message]),
      (answerText) => {
        setMessages((prev) => [...prev, { role: "assistant", text: answerText }]);
        setProgressLog([]);
        setSending(false);
      },
      (message) => {
        setError(`${message} — this needs a live GROQ_API_KEY configured in backend/.env. See TESTING.md.`);
        setProgressLog([]);
        setSending(false);
      },
    );
  }

  return (
    <div className="page">
      <h2>Ask about your portfolio</h2>
      <p className="muted">
        Read-only — chat can look up and explain what the digest already found, but can't
        save new insights. Every number it states comes from a tool call made this turn;
        if it can't verify its own answer, it shows you the raw data instead of guessing.
      </p>

      {error && <div className="banner banner-error">{error}</div>}

      <section className="card chat-panel">
        <div className="chat-log">
          {messages.length === 0 && (
            <div className="chat-empty">
              <p className="muted">Try asking:</p>
              <div className="chat-examples">
                {EXAMPLE_QUESTIONS.map((q) => (
                  <button key={q} className="link-button" onClick={() => ask(q)} disabled={sending}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`chat-message chat-message-${m.role}`}>
              <span className="chat-message-role">{m.role === "user" ? "You" : "Agent"}</span>
              <p>{m.text}</p>
            </div>
          ))}
          {sending && (
            <div className="chat-message chat-message-assistant chat-progress">
              <span className="chat-message-role">Agent</span>
              {progressLog.length === 0 ? (
                <p className="muted">Connecting…</p>
              ) : (
                progressLog.map((line, i) => (
                  <p key={i} className="muted">
                    {line}
                  </p>
                ))
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <form
          className="chat-input-row"
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about a symbol or your portfolio…"
            disabled={sending}
          />
          <button type="submit" disabled={sending || !input.trim()}>
            {sending ? "Asking…" : "Ask"}
          </button>
        </form>
      </section>
    </div>
  );
}

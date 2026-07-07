"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api,
  ApiError,
  clearTokens,
  getToken,
  type Chat,
  type ChatMessage,
  type ChatSummary,
} from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";

const STARTERS = [
  "Do I need a data protection officer for a 30-person company?",
  "How long is the legal guarantee when I sell goods to consumers?",
  "Which currently open EU funding calls could my startup apply to?",
  "What interest can I charge when a business customer pays late?",
];

export default function ChatPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [active, setActive] = useState<Chat | null>(null);
  const [pending, setPending] = useState(false);
  const [question, setQuestion] = useState("");
  const [industry, setIndustry] = useState("");
  const [me, setMe] = useState<{ username: string; documents: number } | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  // auth gate + initial load
  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        const [who, health, list] = await Promise.all([
          api.me(),
          api.health(),
          api.listChats(),
        ]);
        setMe({ username: who.username, documents: health.documents });
        setChats(list.conversations);
        setReady(true);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          clearTokens();
          router.replace("/login");
        }
      }
    })();
  }, [router]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [active?.messages.length, pending]);

  const refreshList = useCallback(async () => {
    setChats((await api.listChats()).conversations);
  }, []);

  async function openChat(id: string) {
    setActive(await api.getChat(id));
  }

  async function newChat() {
    const c = await api.createChat();
    await refreshList();
    setActive({ ...c, messages: [] });
    setIndustry(industry); // keep sector context across chats
  }

  async function removeChat(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    await api.deleteChat(id);
    if (active?.id === id) setActive(null);
    await refreshList();
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || pending) return;
    let chat = active;
    if (!chat) {
      const c = await api.createChat();
      chat = { ...c, messages: [] };
      setActive(chat);
    }
    const userMsg: ChatMessage = {
      role: "user",
      content: q,
      citations: [],
      meta: {},
      created_at: Date.now() / 1000,
    };
    setActive({ ...chat, messages: [...chat.messages, userMsg] });
    setQuestion("");
    setPending(true);
    try {
      const ans = await api.ask(chat.id, q, industry || undefined);
      const asstMsg: ChatMessage = {
        role: "assistant",
        content: ans.answer,
        citations: ans.citations,
        meta: { mode: ans.mode, escalated: ans.escalated, insufficient: ans.insufficient },
        created_at: Date.now() / 1000,
      };
      setActive((cur) =>
        cur ? { ...cur, messages: [...cur.messages, asstMsg] } : cur
      );
      await refreshList();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Request failed";
      setActive((cur) =>
        cur
          ? {
              ...cur,
              messages: [
                ...cur.messages,
                { role: "assistant", content: `_${msg}_`, citations: [], meta: {}, created_at: 0 },
              ],
            }
          : cur
      );
    } finally {
      setPending(false);
    }
  }

  function logout() {
    clearTokens();
    router.replace("/login");
  }

  if (!ready) {
    return (
      <div className="auth-wrap">
        <p className="pending">
          Loading<span className="spin"><span>.</span><span>.</span><span>.</span></span>
        </p>
      </div>
    );
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-head">
          <span className="brand">
            EURAG<span className="star">★</span>
          </span>
        </div>
        <button className="new-chat" onClick={newChat}>
          New chat
        </button>
        <nav className="chat-list">
          {chats.map((c) => (
            <div
              key={c.id}
              className={"chat-item" + (active?.id === c.id ? " active" : "")}
              onClick={() => openChat(c.id)}
            >
              <span className="title">{c.title}</span>
              <button className="x" title="Delete" onClick={(e) => removeChat(c.id, e)}>
                ✕
              </button>
            </div>
          ))}
          {chats.length === 0 && (
            <p style={{ padding: "10px", color: "var(--muted)", fontSize: 13 }}>
              No saved chats yet.
            </p>
          )}
        </nav>
        <div className="sidebar-foot">
          <span>{me?.username}</span>
          <button onClick={logout}>sign out</button>
        </div>
      </aside>

      <main className="pane">
        <div className="pane-head">
          <span>{active ? active.title : "EU SME Intelligence Hub"}</span>
          <span className="badge">{me?.documents} official texts indexed</span>
        </div>

        <div className="thread" ref={threadRef}>
          <div className="thread-inner">
            {!active || active.messages.length === 0 ? (
              <div className="empty">
                <p className="lede">Ask anything an EU regulation should answer.</p>
                <div className="cards">
                  {STARTERS.map((s) => (
                    <button key={s} onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              active.messages.map((m, i) => <Message key={i} msg={m} />)
            )}
            {pending && (
              <p className="pending">
                Consulting the corpus
                <span className="spin"><span>.</span><span>.</span><span>.</span></span>
                {" "}— hard questions escalate to a stronger model.
              </p>
            )}
          </div>
        </div>

        <div className="composer">
          <div className="composer-inner">
            <div className="industry-row">
              <label htmlFor="ind">Industry · optional</label>
              <input
                id="ind"
                value={industry}
                onChange={(e) => setIndustry(e.target.value)}
                placeholder="e.g. software, food, manufacturing…"
                maxLength={80}
              />
            </div>
            <div className="inputrow">
              <textarea
                rows={1}
                value={question}
                placeholder="Ask a compliance or funding question…"
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(question);
                  }
                }}
              />
              <button onClick={() => send(question)} disabled={pending}>
                Ask
              </button>
            </div>
            <p className="disclaimer">
              Information, not legal advice · every claim links to an official source
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}

function Message({ msg }: { msg: ChatMessage }) {
  const ref = useRef<HTMLDivElement>(null);

  function onMarkerClick(e: React.MouseEvent) {
    const t = e.target as HTMLElement;
    if (t.classList.contains("marker")) {
      const fn = ref.current?.querySelector(`.cite[data-m="${t.dataset.m}"]`);
      if (fn) {
        fn.scrollIntoView({ behavior: "smooth", block: "center" });
        fn.classList.add("flash");
        setTimeout(() => fn.classList.remove("flash"), 1600);
      }
    }
  }

  if (msg.role === "user") {
    return (
      <div className="msg user">
        <div className="who">You</div>
        <div className="bubble">{msg.content}</div>
      </div>
    );
  }

  return (
    <div className="msg" ref={ref}>
      <div className="who">EURAG</div>
      <div className="answer" onClick={onMarkerClick}>
        <div dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }} />
        {(msg.meta.escalated || msg.meta.insufficient || msg.meta.mode) && (
          <div className="flags">
            {msg.meta.mode && <span className="flag">mode: {msg.meta.mode}</span>}
            {msg.meta.escalated && <span className="flag escalated">★ escalated</span>}
            {msg.meta.insufficient && <span className="flag insufficient">sources incomplete</span>}
          </div>
        )}
        {msg.citations.length > 0 && (
          <div className="cites">
            <div className="lbl">Sources</div>
            {msg.citations.map((c) => (
              <div className="cite" data-m={c.marker} key={c.marker}>
                <span className="fn">[{c.marker}]</span>
                <span>
                  <span className="t">{c.title}</span>
                  {c.source_url && (
                    <a href={c.source_url} target="_blank" rel="noopener noreferrer">
                      official text ↗
                    </a>
                  )}
                  <span className="q">“{c.quote}…”</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

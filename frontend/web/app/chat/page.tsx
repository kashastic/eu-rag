"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  clearTokens,
  getToken,
  type Account,
  type Chat,
  type ChatMessage,
  type ChatSummary,
  type HistoryTurn,
} from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";
import {
  Turnstile,
  TurnstileUnavailableError,
  type TurnstileHandle,
} from "@/components/Turnstile";

const STARTERS = [
  "Do I need a data protection officer for a 30-person company?",
  "How long is the legal guarantee when I sell goods to consumers?",
  "Which currently open EU funding calls could my startup apply to?",
  "What interest can I charge when a business customer pays late?",
];

export default function ChatPage() {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [account, setAccount] = useState<Account | null>(null);
  const [documents, setDocuments] = useState<number>(0);

  // authed mode: saved chats
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [active, setActive] = useState<Chat | null>(null);
  // anonymous mode: ephemeral thread
  const [anonMsgs, setAnonMsgs] = useState<ChatMessage[]>([]);
  const [anonRemaining, setAnonRemaining] = useState<number | null>(null);
  // bot gate: sitekey comes from /healthz at runtime. The widget is invisible
  // and only runs at submit time, so nothing here gates the composer.
  const [sitekey, setSitekey] = useState<string | null>(null);
  const tsRef = useRef<TurnstileHandle>(null);
  // true only while Cloudflare has an interactive challenge on screen
  const [challenging, setChallenging] = useState(false);

  const [pending, setPending] = useState(false);
  const [question, setQuestion] = useState("");
  const [industry, setIndustry] = useState("");
  const [loginOpen, setLoginOpen] = useState(false);
  const [loginForced, setLoginForced] = useState(false);
  const [loginMode, setLoginMode] = useState<"login" | "register">("login");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);

  const init = useCallback(async () => {
    const health = await api.health().catch(() => null);
    if (health) {
      setDocuments(health.documents);
      setSitekey(health.turnstile_sitekey ?? null);
    }
    if (getToken()) {
      try {
        const [acct, list] = await Promise.all([api.account(), api.listChats()]);
        setAccount(acct);
        setChats(list.conversations);
        setAuthed(true);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) clearTokens();
        setAuthed(false);
      }
    } else {
      setAuthed(false);
      // /login redirects here with ?auth=login|register — one sign-in UI, one
      // place to keep working. Read off location rather than useSearchParams so
      // this client page needs no Suspense boundary to build.
      const want = new URLSearchParams(window.location.search).get("auth");
      if (want === "login" || want === "register") {
        setLoginMode(want);
        setLoginOpen(true);
        window.history.replaceState(null, "", window.location.pathname);
      }
    }
    setReady(true);
  }, []);

  useEffect(() => {
    init();
  }, [init]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [active?.messages.length, anonMsgs.length, pending]);

  const messages: ChatMessage[] = authed ? active?.messages ?? [] : anonMsgs;

  async function onLoggedIn() {
    setLoginOpen(false);
    setLoginForced(false);
    setAnonMsgs([]);
    setAnonRemaining(null);
    setActive(null);
    await init();
  }

  function logout() {
    clearTokens();
    setAuthed(false);
    setAccount(null);
    setChats([]);
    setActive(null);
  }

  // ---- authed saved-chat helpers ----
  const refreshList = useCallback(async () => {
    setChats((await api.listChats()).conversations);
  }, []);
  async function openChat(id: string) {
    setActive(await api.getChat(id));
  }
  async function newChat() {
    setActive(null);
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
    setQuestion("");
    const userMsg: ChatMessage = {
      role: "user",
      content: q,
      citations: [],
      meta: {},
      created_at: Date.now() / 1000,
    };

    if (!authed) {
      setAnonMsgs((m) => [...m, userMsg]);
      setPending(true);
      try {
        // one fresh single-use token per question, minted here rather than on
        // page load — the visitor only ever sees the widget if Cloudflare asks
        const token = sitekey ? await tsRef.current?.getToken() : undefined;
        // anonMsgs here is still the pre-question state, which is exactly the
        // history to resolve this follow-up against
        const ans = await api.queryAnon(q, industry || undefined, token, toHistory(anonMsgs));
        setAnonMsgs((m) => [...m, answerToMsg(ans)]);
        if (typeof ans.anon_remaining === "number") setAnonRemaining(ans.anon_remaining);
      } catch (err) {
        if (err instanceof ApiError && err.code === "anonymous_limit_reached") {
          setLoginForced(true);
          setLoginMode("register");
          setLoginOpen(true);
        } else {
          setAnonMsgs((m) => [...m, errMsg(err)]);
        }
      } finally {
        setPending(false);
      }
      return;
    }

    // authed: ensure a saved chat exists, then ask within it
    let chat = active;
    if (!chat) {
      const c = await api.createChat();
      chat = { ...c, messages: [] };
      setActive(chat);
    }
    setActive({ ...chat, messages: [...chat.messages, userMsg] });
    setPending(true);
    try {
      const ans = await api.ask(chat.id, q, industry || undefined);
      setActive((cur) => (cur ? { ...cur, messages: [...cur.messages, answerToMsg(ans)] } : cur));
      await refreshList();
    } catch (err) {
      setActive((cur) => (cur ? { ...cur, messages: [...cur.messages, errMsg(err)] } : cur));
    } finally {
      setPending(false);
    }
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

  const empty = messages.length === 0;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-head">
          <span className="brand">EURAG<span className="star">★</span></span>
        </div>
        {authed ? (
          <>
            <button className="new-chat" onClick={newChat}>New chat</button>
            <nav className="chat-list">
              {chats.map((c) => (
                <div
                  key={c.id}
                  className={"chat-item" + (active?.id === c.id ? " active" : "")}
                  onClick={() => openChat(c.id)}
                >
                  <span className="title">{c.title}</span>
                  <button className="x" onClick={(e) => removeChat(c.id, e)}>✕</button>
                </div>
              ))}
              {chats.length === 0 && (
                <p style={{ padding: 10, color: "var(--muted)", fontSize: 13 }}>No saved chats yet.</p>
              )}
            </nav>
            <div className="sidebar-foot">
              <span>{account?.username}</span>
              <button onClick={logout}>sign out</button>
            </div>
          </>
        ) : (
          <div className="anon-side">
            <p>You&apos;re browsing anonymously. Your chats aren&apos;t saved.</p>
            <button
              className="btn"
              onClick={() => {
                setLoginForced(false);
                setLoginMode("login");
                setLoginOpen(true);
              }}
            >
              Sign in to save chats
            </button>
          </div>
        )}
      </aside>

      <main className="pane">
        <div className="pane-head">
          <span>{authed ? (active ? active.title : "New chat") : "EU SME Intelligence Hub"}</span>
          <span className="badge">{documents} official texts indexed</span>
        </div>

        {authed && account?.tier === "free" && (
          <div className="tier-banner">
            You&apos;re on the free tier — a cheaper model answers your queries.
            <button onClick={() => setSettingsOpen(true)}>Add your Anthropic key for premium models →</button>
          </div>
        )}

        <div className="thread" ref={threadRef}>
          <div className="thread-inner">
            {empty ? (
              <div className="empty">
                <p className="lede">Ask anything an EU regulation should answer.</p>
                <div className="cards">
                  {STARTERS.map((s) => (
                    <button key={s} onClick={() => send(s)}>{s}</button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m, i) => <Message key={i} msg={m} />)
            )}
            {pending && (
              <p className="pending">
                {challenging ? "Waiting for the Cloudflare check below" : "Consulting the corpus"}
                <span className="spin"><span>.</span><span>.</span><span>.</span></span>
              </p>
            )}
          </div>
        </div>

        <div className="composer">
          <div className="composer-inner">
            {!authed && sitekey && (
              <Turnstile ref={tsRef} sitekey={sitekey} onInteractive={setChallenging} />
            )}
            <div className="industry-row">
              <label htmlFor="ind">Industry · optional</label>
              <input
                id="ind"
                value={industry}
                onChange={(e) => setIndustry(e.target.value)}
                placeholder="e.g. software, food, manufacturing…"
                maxLength={80}
              />
              {!authed && anonRemaining !== null && (
                <span className="anon-left">{anonRemaining} free question{anonRemaining === 1 ? "" : "s"} left</span>
              )}
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
            <p className="disclaimer">Information, not legal advice · every claim links to an official source</p>
          </div>
        </div>
      </main>

      {loginOpen && (
        <LoginModal
          forced={loginForced}
          initialMode={loginMode}
          sitekey={sitekey}
          onClose={() => setLoginOpen(false)}
          onSuccess={onLoggedIn}
        />
      )}
      {settingsOpen && account && (
        <SettingsModal
          account={account}
          onClose={() => setSettingsOpen(false)}
          onChanged={async () => setAccount(await api.account())}
        />
      )}
    </div>
  );
}

/** Pairs the rendered transcript back into (question, answer) turns for the
 *  backend. Only the anonymous path needs this — logged-in chats are stored
 *  server-side, so that route reads its own history. A question whose answer
 *  errored keeps an empty answer: the topic still helps resolve the next
 *  follow-up. */
function toHistory(msgs: ChatMessage[]): HistoryTurn[] {
  const turns: HistoryTurn[] = [];
  msgs.forEach((msg, i) => {
    if (msg.role !== "user") return;
    const next = msgs[i + 1];
    turns.push({
      question: msg.content,
      answer: next && next.role === "assistant" ? next.content : "",
    });
  });
  return turns;
}

function answerToMsg(ans: Awaited<ReturnType<typeof api.queryAnon>>): ChatMessage {
  return {
    role: "assistant",
    content: ans.answer,
    citations: ans.citations,
    meta: { mode: ans.mode, escalated: ans.escalated, insufficient: ans.insufficient },
    created_at: Date.now() / 1000,
  };
}
function errMsg(err: unknown): ChatMessage {
  const m =
    err instanceof ApiError || err instanceof TurnstileUnavailableError
      ? err.message
      : "Request failed";
  // renderMarkdown only italicises *…*, not _…_ — underscores would show raw
  return { role: "assistant", content: `*${m}*`, citations: [], meta: {}, created_at: 0 };
}

function LoginModal({
  forced,
  initialMode,
  sitekey,
  onClose,
  onSuccess,
}: {
  forced: boolean;
  initialMode: "login" | "register";
  sitekey: string | null;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [mode, setMode] = useState<"login" | "register">(forced ? "register" : initialMode);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // bot gate on account creation (login is not gated). Invisible: it runs on
  // submit, so the button below is never disabled waiting for a challenge.
  const tsRef = useRef<TurnstileHandle>(null);
  const [challenging, setChallenging] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (mode === "register") {
        const token = sitekey ? await tsRef.current?.getToken() : undefined;
        await api.register(username, password, token);
      }
      await api.login(username, password);
      onSuccess();
    } catch (err) {
      setError(
        err instanceof ApiError || err instanceof TurnstileUnavailableError
          ? err.message
          : "Something went wrong"
      );
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={() => (forced ? null : onClose())}>
      <form className="auth-card modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h1 className="brand">EURAG<span className="star">★</span></h1>
        <p className="tag">
          {forced
            ? "You've used your free questions. Create an account to keep going — your chats will be saved."
            : "Sign in to save your chats across sessions."}
        </p>
        <div className="field">
          <label htmlFor="mu">Username</label>
          <input id="mu" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </div>
        <div className="field">
          <label htmlFor="mp">Password {mode === "register" && "(min 10 chars)"}</label>
          <input id="mp" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {mode === "register" && sitekey && (
          <Turnstile ref={tsRef} sitekey={sitekey} onInteractive={setChallenging} />
        )}
        {challenging && <p className="hint">Complete the check above to continue.</p>}
        {error && <p className="err">{error}</p>}
        <button className="btn" type="submit" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
        <button className="btn google" type="button" disabled title="Configure a Google OAuth client to enable">
          Continue with Google (coming soon)
        </button>
        <p className="switch">
          {mode === "login" ? "New here? " : "Have an account? "}
          <button type="button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
            {mode === "login" ? "Create an account" : "Sign in"}
          </button>
        </p>
        {!forced && <p className="switch"><button type="button" onClick={onClose}>Keep browsing anonymously</button></p>}
      </form>
    </div>
  );
}

function SettingsModal({
  account,
  onClose,
  onChanged,
}: {
  account: Account;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setError("");
    setBusy(true);
    try {
      await api.setApiKey(key.trim());
      await onChanged();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save key");
      setBusy(false);
    }
  }
  async function remove() {
    setBusy(true);
    await api.clearApiKey();
    await onChanged();
    onClose();
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="auth-card modal" onClick={(e) => e.stopPropagation()}>
        <h1 className="brand" style={{ fontSize: 22 }}>Your Anthropic key</h1>
        <p className="tag">
          Premium models (Sonnet, with escalation to Opus on hard questions) run on your own
          Anthropic key, billed to you. Stored encrypted; never shown again.
        </p>
        {!account.byok_available && (
          <p className="err">This server isn&apos;t configured for key storage (no encryption key set).</p>
        )}
        {account.has_api_key ? (
          <>
            <p style={{ fontSize: 14, color: "var(--ink-soft)" }}>
              A key is saved — you&apos;re on the <strong>premium</strong> tier.
            </p>
            <button className="btn" onClick={remove} disabled={busy}>Remove key (back to free)</button>
          </>
        ) : (
          <>
            <div className="field">
              <label htmlFor="ak">API key</label>
              <input id="ak" value={key} placeholder="sk-ant-…" onChange={(e) => setKey(e.target.value)}
                disabled={!account.byok_available} />
            </div>
            {error && <p className="err">{error}</p>}
            <button className="btn" onClick={save} disabled={busy || !account.byok_available || key.length < 20}>
              {busy ? "…" : "Save key"}
            </button>
          </>
        )}
        <p className="switch"><button type="button" onClick={onClose}>Close</button></p>
      </div>
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
                    <a href={c.source_url} target="_blank" rel="noopener noreferrer">official text ↗</a>
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

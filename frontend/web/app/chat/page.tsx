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
  HISTORY_ANSWER_CHARS,
  type HistoryTurn,
} from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";
import {
  Turnstile,
  TurnstileUnavailableError,
  type TurnstileHandle,
} from "@/components/Turnstile";
import { GoogleSignIn } from "@/components/GoogleSignIn";
import { ProfileFields, ProfileIntro, ProfileSummary } from "@/components/ProfileFields";
import * as profileStore from "@/lib/profile";
import { type Profile } from "@/lib/profile";

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
  const [googleClientId, setGoogleClientId] = useState<string | null>(null);
  const tsRef = useRef<TurnstileHandle>(null);
  // true only while Cloudflare has an interactive challenge on screen
  const [challenging, setChallenging] = useState(false);

  const [pending, setPending] = useState(false);
  const [question, setQuestion] = useState("");
  // Business context. Read from localStorage on mount (never during render —
  // the server has no localStorage and the markup must match), then overwritten
  // by the account's stored copy if there is one, so a second device recovers
  // it. Sent with every question; the server never looks it up per query.
  const [profile, setProfile] = useState<Profile>(profileStore.EMPTY_PROFILE);
  // Decided once, at load, and NOT derived from `profile` being empty — that
  // would tear the block off the screen the moment the first dropdown was set,
  // leaving the other three unreachable. Starts false so it can't flash before
  // init has read localStorage.
  const [showIntro, setShowIntro] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [loginForced, setLoginForced] = useState(false);
  const [loginMode, setLoginMode] = useState<"login" | "register">("login");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  // Narrow screens only: the sidebar is off-canvas there and this opens it.
  // Below 720px it used to be `display: none` with nothing in its place, which
  // took New chat, the saved-chat list, the account and sign-out off the phone
  // entirely — and anonymously it took away the only "Sign in" affordance
  // there was. Always false on desktop, where the sidebar is simply present.
  const [navOpen, setNavOpen] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);

  const init = useCallback(async () => {
    const health = await api.health().catch(() => null);
    if (health) {
      setDocuments(health.documents);
      setSitekey(health.turnstile_sitekey ?? null);
      setGoogleClientId(health.google_client_id ?? null);
    }
    const local = profileStore.load();
    setProfile(local);
    setShowIntro(!profileStore.isDismissed() && profileStore.isEmpty(local));
    if (getToken()) {
      try {
        const [acct, list] = await Promise.all([api.account(), api.listChats()]);
        setAccount(acct);
        setChats(list.conversations);
        setAuthed(true);
        // the account's copy wins on a fresh device, but an empty stored
        // profile must not wipe one the visitor set before signing in
        if (acct.profile && !profileStore.isEmpty(acct.profile)) {
          setProfile(acct.profile);
          profileStore.save(acct.profile);
        } else if (!profileStore.isEmpty(local)) {
          api.saveProfile(local).catch(() => {});
        }
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

  // Escape closes the drawer, like every other overlay on the page. Bound only
  // while it is open so the listener isn't live for the whole session.
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  // The `anonMsgs` fallback is the safety net for a failed import: the turns are
  // still in state, so show them instead of pretending the conversation never
  // happened. They are read-only at that point — the next question opens a
  // fresh saved chat — but nothing is silently lost.
  const messages: ChatMessage[] = authed
    ? active?.messages ?? (anonMsgs.length ? anonMsgs : [])
    : anonMsgs;

  async function onLoggedIn() {
    setLoginOpen(false);
    setLoginForced(false);
    setAnonRemaining(null);

    // Carry the anonymous thread into the new account BEFORE clearing it. The
    // login wall fires because the free questions ran out, so this is exactly
    // the conversation that prompted the sign-up — dropping it was the whole
    // bug. Import failure keeps the messages in state rather than discarding
    // them (see `messages` below), so a network blip cannot lose the thread.
    let adopted: Chat | null = null;
    if (anonMsgs.length > 0) {
      try {
        adopted = await api.importChat(anonMsgs);
        setAnonMsgs([]);
      } catch {
        adopted = null;
      }
    }
    setActive(adopted);
    await init();
    if (adopted) await refreshList();
  }

  function logout() {
    clearTokens();
    setAuthed(false);
    setAccount(null);
    setChats([]);
    setActive(null);
    setNavOpen(false);
  }

  // ---- authed saved-chat helpers ----
  const refreshList = useCallback(async () => {
    setChats((await api.listChats()).conversations);
  }, []);
  // Every one of these closes the drawer: on a phone it covers the thread, so
  // picking a chat and being left staring at the list is a dead end. Deleting
  // is the exception — you may well delete several.
  async function openChat(id: string) {
    setNavOpen(false);
    setActive(await api.getChat(id));
  }
  async function newChat() {
    setNavOpen(false);
    setActive(null);
  }
  async function removeChat(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    await api.deleteChat(id);
    if (active?.id === id) setActive(null);
    await refreshList();
  }

  // one writer for the profile: local first (so it survives a reload even when
  // the account call fails), then best-effort to the account. A failed sync
  // must never block asking a question, so it is deliberately not awaited.
  function updateProfile(next: Profile) {
    setProfile(next);
    profileStore.save(next);
    if (authed) api.saveProfile(next).catch(() => {});
  }

  function skipProfile() {
    profileStore.dismiss();
    setShowIntro(false);
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
        const ans = await api.queryAnon(
          q,
          profileStore.forRequest(profile),
          token,
          toHistory(anonMsgs)
        );
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
      const ans = await api.ask(chat.id, q, profileStore.forRequest(profile));
      setActive((cur) => (cur ? { ...cur, messages: [...cur.messages, answerToMsg(ans)] } : cur));
      if (typeof ans.free_remaining === "number") {
        setAccount((a) => (a ? { ...a, free_remaining: ans.free_remaining! } : a));
      }
      await refreshList();
    } catch (err) {
      // free allowance spent — the way on is BYOK, so open the key dialog
      // rather than leaving an error the user can't act on
      if (err instanceof ApiError && err.code === "free_limit_reached") {
        setSettingsOpen(true);
        setAccount((a) => (a ? { ...a, free_remaining: 0 } : a));
      }
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
      {/* Below 720px the sidebar is off-canvas, so it needs something to
          dismiss it that isn't the toggle — a tap on the thread behind it is
          what a phone user reaches for first. Inert (display:none) at desktop
          widths, where the sidebar is always on screen. */}
      <div
        className={"nav-scrim" + (navOpen ? " open" : "")}
        onClick={() => setNavOpen(false)}
        aria-hidden="true"
      />
      <aside className={"sidebar" + (navOpen ? " open" : "")} id="sidebar">
        <div className="sidebar-head">
          <span className="brand">EURAG<span className="star">★</span></span>
          {/* the drawer's own close control: on a phone the toggle in the
              masthead is behind the drawer once it is open */}
          <button
            className="nav-close"
            onClick={() => setNavOpen(false)}
            aria-label="Close menu"
          >
            ✕
          </button>
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
                setNavOpen(false);
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
          <button
            className="nav-toggle"
            onClick={() => setNavOpen(true)}
            aria-label="Open menu"
            aria-expanded={navOpen}
            aria-controls="sidebar"
          >
            <span />
            <span />
            <span />
          </button>
          <span>{authed ? (active ? active.title : "New chat") : "EURAG"}</span>
          <span className="badge">
            {documents > 0 && <>{documents} official texts</>}
            <span className="tagline">
              {documents > 0 ? " · " : ""}every claim cited
            </span>
          </span>
        </div>

        {authed && account?.tier === "free" && (
          <div className="tier-banner">
            {account.free_remaining === 0 ? (
              <>You&apos;ve used all {account.free_limit} free questions.</>
            ) : (
              <>
                Free tier — a cheaper model, and{" "}
                <strong>
                  {account.free_remaining} of {account.free_limit} questions
                </strong>{" "}
                left.
              </>
            )}
            <button onClick={() => setSettingsOpen(true)}>
              Add your Anthropic key for premium models →
            </button>
          </div>
        )}

        <div className="thread" ref={threadRef}>
          <div className="thread-inner">
            {empty ? (
              <div className="empty">
                <Opening documents={documents} />
                {/* Starters before the context block on purpose: asking is the
                    thing to do here, and the profile is an optional refinement.
                    Above the starters it pushed them off a laptop screen, which
                    put a form where the primary action should be. */}
                <div className="starters">
                  <div className="starters-label">Start with one of these</div>
                  <div className="cards">
                    {STARTERS.map((s) => (
                      <button key={s} onClick={() => send(s)}>{s}</button>
                    ))}
                  </div>
                </div>
                {/* Never in the way: the composer stays enabled throughout, so
                    a visitor who ignores this entirely loses nothing. */}
                {showIntro && (
                  <ProfileIntro
                    profile={profile}
                    onChange={updateProfile}
                    onSkip={skipProfile}
                  />
                )}
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
              {profileStore.isEmpty(profile) ? (
                <button className="profile-open" onClick={() => setProfileOpen(true)}>
                  + Add your business context
                </button>
              ) : (
                <ProfileSummary profile={profile} onEdit={() => setProfileOpen(true)} />
              )}
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
            {/* the disclaimer is on every screen anonymous or not, so it is
                also where the legal pages hang — no separate footer needed */}
            {/* Borrowing a law firm's clothes raises the stakes on this line,
                so it is stamped rather than murmured. */}
            <p className="disclaimer">
              <a href="/terms" className="stamp">Not legal advice</a>
              <span>Every claim links to an official source</span>
              <a href="/privacy">Privacy</a>
            </p>
          </div>
        </div>
      </main>

      {loginOpen && (
        <LoginModal
          forced={loginForced}
          initialMode={loginMode}
          sitekey={sitekey}
          googleClientId={googleClientId}
          onClose={() => setLoginOpen(false)}
          onSuccess={onLoggedIn}
        />
      )}
      {/* Not folded into SettingsModal on purpose: that one only renders when
          `account` is set, so an anonymous visitor clicking "edit" would have
          got a dead button — and anonymous is the default path here. One
          dialog, reachable in both states, one copy of the state. */}
      {profileOpen && (
        <ProfileModal
          profile={profile}
          onChange={updateProfile}
          onClose={() => setProfileOpen(false)}
        />
      )}
      {settingsOpen && account && (
        <SettingsModal
          account={account}
          onClose={() => setSettingsOpen(false)}
          onChanged={async () => setAccount(await api.account())}
          // the account is gone server-side; drop the local session with it so
          // the UI can't keep showing a signed-in state backed by nothing
          onDeleted={() => {
            setSettingsOpen(false);
            logout();
          }}
        />
      )}
    </div>
  );
}

/** Three real documents from the corpus, cited by the opening statement.
 *  They have to be real: the point of the rail is that it behaves exactly like
 *  the citation block under an answer, so a visitor learns the apparatus before
 *  they have asked anything. CELEX ids are the corpus's own identifiers. */
const OPENING_SOURCES = [
  {
    doc: "Regulation (EU) 2016/679",
    name: "General Data Protection Regulation",
    celex: "32016R0679",
  },
  {
    doc: "Regulation (EU) 2024/1689",
    name: "Artificial Intelligence Act",
    celex: "32024R1689",
  },
  {
    doc: "Directive 2011/7/EU",
    name: "Late payment in commercial transactions",
    celex: "32011L0007",
  },
];

/** The home screen explains what EURAG is by demonstrating it: the statement
 *  carries citation markers, and they resolve. Hovering or tabbing a marker
 *  lights its footnote, and hovering a footnote lights its marker — the same
 *  two-way link the answers use. */
function Opening({ documents }: { documents: number }) {
  const [active, setActive] = useState<number | null>(null);

  const ref = (n: number) => (
    <button
      type="button"
      className={"ref" + (active === n ? " on" : "")}
      aria-describedby={`fn-${n}`}
      onMouseEnter={() => setActive(n)}
      onMouseLeave={() => setActive(null)}
      onFocus={() => setActive(n)}
      onBlur={() => setActive(null)}
    >
      {n}
    </button>
  );

  return (
    <section className="opening">
      <div className="opening-statement">
        <h1 className="statement">
          Every claim points to an article — <em>or it isn&apos;t made.</em>
        </h1>
        <p className="standfirst">
          EURAG answers compliance and funding questions for small businesses in
          the EU. Ask in plain language and get back an answer where every
          sentence resolves to a numbered article in an official text — the
          GDPR{ref(1)}, the AI Act{ref(2)}, the late-payment rules{ref(3)}
          {/* The count is only stated when we actually have it. A failing
              /healthz reports 0, and "0 documents in all" set in the opening
              sentence is a far worse failure than a blank counter. */}
          {documents > 0 && (
            <>
              {" "}&mdash; <strong>{documents} documents in all</strong>
            </>
          )}
          . When the corpus doesn&apos;t cover your question, it says so instead
          of guessing.
        </p>
        <p className="standfirst">
          It is a research tool, not a lawyer. It shows you the text and where it
          came from; what you do with that is your call.
        </p>
      </div>

      <aside className="footnotes">
        <div className="footnotes-label">Cited above</div>
        {OPENING_SOURCES.map((s, i) => (
          <div
            key={s.celex}
            id={`fn-${i + 1}`}
            className={"footnote" + (active === i + 1 ? " on" : "")}
            onMouseEnter={() => setActive(i + 1)}
            onMouseLeave={() => setActive(null)}
          >
            <span className="n">{i + 1}</span>
            <span>
              <span className="doc">{s.doc}</span>
              {s.name}
              <br />
              <span className="celex">CELEX {s.celex}</span>
            </span>
          </div>
        ))}
      </aside>
    </section>
  );
}

function ProfileModal({
  profile,
  onChange,
  onClose,
}: {
  profile: Profile;
  onChange: (next: Profile) => void;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="auth-card modal" onClick={(e) => e.stopPropagation()}>
        <h2>Your business context</h2>
        <p className="muted">
          Every field is optional. EURAG uses this to work out which thresholds
          in the sources apply to you — it never invents an obligation the
          sources don&apos;t support.
        </p>
        <ProfileFields profile={profile} onChange={onChange} />
        <button className="btn" onClick={onClose}>
          Done
        </button>
      </div>
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
      // trimmed: only the topic of a prior turn matters to the rewrite, and
      // sending whole answers is what used to push the request past the
      // server's cap on the third question of a thread
      answer:
        next && next.role === "assistant"
          ? next.content.slice(0, HISTORY_ANSWER_CHARS)
          : "",
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
  googleClientId,
  onClose,
  onSuccess,
}: {
  forced: boolean;
  initialMode: "login" | "register";
  sitekey: string | null;
  googleClientId: string | null;
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

  // The same bounds the API enforces (api/routes/auth.Credentials). Checked here
  // so a short password is answered instantly instead of costing a round trip
  // that comes back as a pydantic validation error.
  function invalid(): string | null {
    const name = username.trim();
    if (name.length < 3 || name.length > 40) return "Username must be 3–40 characters.";
    if (mode === "register" && !/^[a-zA-Z0-9_]+$/.test(name)) {
      return "Username can use letters, digits and underscores only.";
    }
    if (password.length < 10) return "Password must be at least 10 characters.";
    return null;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const problem = invalid();
    if (problem) {
      setError(problem);
      return;
    }
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
      const message =
        err instanceof ApiError || err instanceof TurnstileUnavailableError
          ? err.message
          : "";
      // never leave the button looking dead: an empty message renders nothing
      setError(message || "Something went wrong. Please try again.");
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
        {googleClientId && (
          <>
            <div className="or">or</div>
            <GoogleSignIn
              clientId={googleClientId}
              onCredential={async (credential) => {
                setError("");
                setBusy(true);
                try {
                  await api.googleLogin(credential);
                  onSuccess();
                } catch (err) {
                  setError(
                    err instanceof ApiError && err.message
                      ? err.message
                      : "Google sign-in failed. Please try again."
                  );
                  setBusy(false);
                }
              }}
              onError={setError}
            />
          </>
        )}
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
  onDeleted,
}: {
  account: Account;
  onClose: () => void;
  onChanged: () => Promise<void>;
  onDeleted: () => void;
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

  const days =
    account.api_key_set_at != null
      ? Math.floor((Date.now() / 1000 - account.api_key_set_at) / 86400)
      : null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="auth-card modal" onClick={(e) => e.stopPropagation()}>
        <h1 className="brand" style={{ fontSize: 22 }}>Your Anthropic key</h1>
        <p className="tag">
          Premium models (Sonnet, escalating to Opus on hard questions) run on your own
          Anthropic key, billed to you — and they lift the {account.free_limit}-question
          free limit.
        </p>
        {!account.byok_available && (
          <p className="err">This server isn&apos;t configured for key storage (no encryption key set).</p>
        )}
        {account.has_api_key ? (
          <>
            <p style={{ fontSize: 14, color: "var(--ink-soft)" }}>
              A key is saved — you&apos;re on the <strong>premium</strong> tier
              {days != null && (
                <>
                  , added <strong>{days === 0 ? "today" : `${days} day${days === 1 ? "" : "s"} ago`}</strong>
                </>
              )}
              .
            </p>
            {days != null && days >= 30 && (
              <p className="hint" style={{ textAlign: "left" }}>
                It&apos;s been a while — consider rotating this key in your Anthropic console.
              </p>
            )}
            <button className="btn" onClick={remove} disabled={busy}>Remove key (back to free)</button>
            <p className="fineprint">
              Removing it here deletes it from this server. It does <strong>not</strong> revoke it
              at Anthropic — rotate or delete it in your{" "}
              <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener noreferrer">
                Anthropic console
              </a>{" "}
              when you&apos;re done.
            </p>
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
            {/* Say plainly what happens to the key. Encryption at rest protects
                against a stolen database, not against whoever runs this server —
                promising more than that would be dishonest. */}
            <div className="fineprint">
              <p>
                <strong>Before you paste one:</strong> the key is encrypted (AES-256-GCM) at rest,
                sent only over HTTPS, never shown again and never returned to your browser. But it
                is stored on this server and decrypted on each question to call Anthropic as you,
                so whoever operates this server can technically read it.
              </p>
              <p>
                Create a <strong>dedicated key with a spend limit</strong> in your{" "}
                <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener noreferrer">
                  Anthropic console
                </a>{" "}
                — never your main key — and revoke it there when you&apos;re done. An Anthropic key
                can&apos;t be scoped to one app.
              </p>
            </div>
          </>
        )}
        <DangerZone account={account} onDeleted={onDeleted} />
        <p className="switch"><button type="button" onClick={onClose}>Close</button></p>
      </div>
    </div>
  );
}

/** Account erasure (GDPR Art. 17), self-service. Two steps on purpose: the
 *  first click only reveals the confirmation, and the server independently
 *  requires the username back — a mis-click can't destroy an account, and the
 *  check doesn't depend on the client behaving. */
function DangerZone({ account, onDeleted }: { account: Account; onDeleted: () => void }) {
  const [arming, setArming] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function confirm() {
    setError("");
    setBusy(true);
    try {
      await api.deleteAccount(typed.trim());
      onDeleted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the account");
      setBusy(false);
    }
  }

  if (!arming) {
    return (
      <div className="danger-zone">
        <button type="button" onClick={() => setArming(true)}>Delete account</button>
      </div>
    );
  }

  return (
    <div className="danger-zone">
      <p>
        This deletes your account, every saved chat, anything you uploaded, your
        stored key and your free-question counter. It is immediate and cannot be
        undone. Questions already answered were sent to Anthropic and are beyond
        this server&apos;s reach — see{" "}
        <a href="/privacy" target="_blank" rel="noopener noreferrer">Privacy</a>.
      </p>
      <div className="field">
        <label htmlFor="confirm-user">
          Type <strong>{account.username}</strong> to confirm
        </label>
        <input
          id="confirm-user"
          value={typed}
          autoComplete="off"
          onChange={(e) => setTyped(e.target.value)}
        />
      </div>
      {error && <p className="err">{error}</p>}
      <button
        type="button"
        onClick={confirm}
        disabled={busy || typed.trim().toLowerCase() !== account.username}
      >
        {busy ? "Deleting…" : "Permanently delete"}
      </button>
      <p className="switch">
        <button type="button" onClick={() => { setArming(false); setTyped(""); setError(""); }}>
          Cancel
        </button>
      </p>
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
        {(msg.meta.escalated || msg.meta.insufficient || msg.meta.mode === "extractive") && (
          <div className="flags">
            {/* mode is only worth surfacing when it is NOT the normal one:
                "llm" is every ordinary answer, so the badge was noise on all of
                them. "extractive" genuinely changes how to read what follows —
                it is text quoted straight from the sources, not written prose.
                "no_sources" already speaks through the insufficient badge. */}
            {msg.meta.mode === "extractive" && (
              <span
                className="flag"
                title="This answer is text quoted directly from the sources, not written by the model."
              >
                verbatim quotes
              </span>
            )}
            {msg.meta.escalated && (
              <span
                className="flag escalated"
                title="The first answer didn't hold up, so a stronger model re-answered it over a deeper search of the corpus."
              >
                ★ stronger model consulted
              </span>
            )}
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

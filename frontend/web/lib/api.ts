// Typed API client for the FastAPI backend. Attaches the bearer token,
// transparently rotates it once on 401 using the refresh token, and clears
// the session if refresh fails.

import type { Profile } from "./profile";

const BASE = process.env.NEXT_PUBLIC_API_URL || "";

const ACCESS = "eurag_access";
const REFRESH = "eurag_refresh";

export type Citation = {
  marker: number;
  title: string;
  source_url: string;
  quote: string;
  chunk_id: string;
};

export type Answer = {
  answer: string;
  citations: Citation[];
  mode: string;
  escalated: boolean;
  insufficient: boolean;
  tier?: "anonymous" | "free" | "byok" | "local";
  anon_remaining?: number;
  /** Free tier only — what's left of the account's lifetime allowance. Absent
   *  on BYOK, which the server doesn't count. */
  free_remaining?: number;
};

export type Account = {
  username: string;
  role: string;
  tier: "free" | "byok" | "local";
  has_api_key: boolean;
  byok_available: boolean;
  free_limit: number;
  free_remaining: number | null;
  /** Epoch seconds when the stored key was last set — drives the rotation
   *  nudge. Never the key itself. */
  api_key_set_at: number | null;
  /** The stored business context, so signing in on a new device restores it. */
  profile: Profile | null;
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  meta: { mode?: string; escalated?: boolean; insufficient?: boolean };
  created_at: number;
};

/** One prior exchange, sent so a follow-up can be resolved before retrieval. */
export type HistoryTurn = { question: string; answer: string };

/** Turns sent with a follow-up. The server trims further; this just keeps the
 *  request small. */
export const HISTORY_TURNS = 3;

export type ChatSummary = { id: string; title: string; updated_at: number };
export type Chat = ChatSummary & { messages: ChatMessage[] };

export function getToken(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(ACCESS);
}
function setTokens(access: string, refresh: string) {
  localStorage.setItem(ACCESS, access);
  localStorage.setItem(REFRESH, refresh);
}
export function clearTokens() {
  localStorage.removeItem(ACCESS);
  localStorage.removeItem(REFRESH);
}

async function refresh(): Promise<boolean> {
  const token = localStorage.getItem(REFRESH);
  if (!token) return false;
  const res = await fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: token }),
  });
  if (!res.ok) return false;
  const data = await res.json();
  setTokens(data.access_token, data.refresh_token);
  return true;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  });
  if (res.status === 401 && retry && (await refresh())) {
    return request<T>(path, init, false);
  }
  if (!res.ok) {
    throw errorFrom(res.status, await res.json().catch(() => ({})));
  }
  return res.json();
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

/** FastAPI speaks three dialects of `detail` and the UI has to survive all of
 *  them: a plain string (`raise HTTPException(detail="…")`), one of our
 *  structured `{code, message}` objects, and — for *request validation*
 *  failures — an **array** of pydantic errors. That array used to fall into the
 *  object branch and yield `undefined`, so e.g. a 9-character password threw an
 *  ApiError with an empty message, `{error && …}` rendered nothing, and the
 *  button looked dead. Every 422 was silent. Always return a non-empty message. */
function errorFrom(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  const fallback = `Request failed (HTTP ${status})`;

  if (typeof detail === "string") return new ApiError(status, detail || fallback);

  if (Array.isArray(detail)) {
    const message = detail
      .map((e) => {
        const loc = Array.isArray(e?.loc) ? e.loc[e.loc.length - 1] : undefined;
        const field = typeof loc === "string" && loc !== "body" ? `${loc}: ` : "";
        return e?.msg ? field + e.msg : "";
      })
      .filter(Boolean)
      .join("; ");
    return new ApiError(status, message || fallback);
  }

  if (detail && typeof detail === "object") {
    const { code, message } = detail as { code?: string; message?: string };
    return new ApiError(status, message || fallback, code);
  }

  return new ApiError(status, fallback);
}

export const api = {
  async register(username: string, password: string, turnstileToken?: string) {
    return request("/auth/register", {
      method: "POST",
      body: JSON.stringify({
        username,
        password,
        ...(turnstileToken ? { turnstile_token: turnstileToken } : {}),
      }),
    });
  },
  async login(username: string, password: string) {
    const data = await request<{ access_token: string; refresh_token: string }>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) }
    );
    setTokens(data.access_token, data.refresh_token);
  },
  /** Exchange a Google ID token for our own session. The token is verified
   *  server-side (signature, audience, issuer, verified email) — the browser
   *  is only a courier. */
  async googleLogin(credential: string) {
    const data = await request<{ access_token: string; refresh_token: string }>(
      "/auth/google",
      { method: "POST", body: JSON.stringify({ credential }) }
    );
    setTokens(data.access_token, data.refresh_token);
  },
  async me() {
    return request<{ username: string; role: string; auth_enabled: boolean }>("/auth/me");
  },
  // Anonymous query. The backend keeps no session, so prior turns are sent
  // with the request: a follow-up ("what if I have 29 people?") carries no
  // topic of its own and would otherwise be retrieved as a fragment. The
  // server caps and trims what it uses; sending the last few turns is enough.
  async queryAnon(
    question: string,
    profile?: Profile,
    turnstileToken?: string,
    history?: HistoryTurn[]
  ) {
    return request<Answer>("/query", {
      method: "POST",
      body: JSON.stringify({
        question,
        ...(profile ? { profile } : {}),
        ...(turnstileToken ? { turnstile_token: turnstileToken } : {}),
        ...(history?.length ? { history: history.slice(-HISTORY_TURNS) } : {}),
      }),
    });
  },
  async account() {
    return request<Account>("/account");
  },
  async setApiKey(apiKey: string) {
    return request("/account/api-key", { method: "PUT", body: JSON.stringify({ api_key: apiKey }) });
  },
  async clearApiKey() {
    return request("/account/api-key", { method: "DELETE" });
  },
  /** Irreversible: account, saved chats, uploaded documents, stored key and
   *  free-tier counter. The server requires the username back as a typed
   *  confirmation — Google accounts have no password to re-enter. */
  async deleteAccount(confirmUsername: string) {
    return request<{ deleted: true; conversations_erased: number; documents_erased: number }>(
      "/account",
      { method: "DELETE", body: JSON.stringify({ confirm_username: confirmUsername }) }
    );
  },
  async health() {
    return request<{
      documents: number;
      auth_enabled: boolean;
      llm: string;
      turnstile_sitekey?: string | null;
      google_client_id?: string | null;
    }>("/healthz");
  },
  async listChats() {
    return request<{ conversations: ChatSummary[] }>("/conversations");
  },
  async createChat() {
    return request<ChatSummary>("/conversations", { method: "POST", body: "{}" });
  },
  async getChat(id: string) {
    return request<Chat>(`/conversations/${id}`);
  },
  async renameChat(id: string, title: string) {
    return request(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },
  async deleteChat(id: string) {
    return request(`/conversations/${id}`, { method: "DELETE" });
  },
  async ask(id: string, question: string, profile?: Profile) {
    return request<Answer>(`/conversations/${id}/messages`, {
      method: "POST",
      body: JSON.stringify(profile ? { question, profile } : { question }),
    });
  },
  /** Persist the profile against the account so a second device recovers it.
   *  Anonymous visitors never call this — their profile stays in the browser. */
  async saveProfile(profile: Profile) {
    return request<{ profile: Profile }>("/account/profile", {
      method: "PUT",
      body: JSON.stringify(profile),
    });
  },
};

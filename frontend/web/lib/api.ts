// Typed API client for the FastAPI backend. Attaches the bearer token,
// transparently rotates it once on 401 using the refresh token, and clears
// the session if refresh fails.

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
};

export type Account = {
  username: string;
  role: string;
  tier: "free" | "byok" | "local";
  has_api_key: boolean;
  byok_available: boolean;
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
  async me() {
    return request<{ username: string; role: string; auth_enabled: boolean }>("/auth/me");
  },
  // Anonymous query. The backend keeps no session, so prior turns are sent
  // with the request: a follow-up ("what if I have 29 people?") carries no
  // topic of its own and would otherwise be retrieved as a fragment. The
  // server caps and trims what it uses; sending the last few turns is enough.
  async queryAnon(
    question: string,
    industry?: string,
    turnstileToken?: string,
    history?: HistoryTurn[]
  ) {
    return request<Answer>("/query", {
      method: "POST",
      body: JSON.stringify({
        question,
        ...(industry ? { industry } : {}),
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
  async health() {
    return request<{
      documents: number;
      auth_enabled: boolean;
      llm: string;
      turnstile_sitekey?: string | null;
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
  async ask(id: string, question: string, industry?: string) {
    return request<Answer>(`/conversations/${id}/messages`, {
      method: "POST",
      body: JSON.stringify(industry ? { question, industry } : { question }),
    });
  },
};

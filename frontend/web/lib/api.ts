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
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  meta: { mode?: string; escalated?: boolean; insufficient?: boolean };
  created_at: number;
};

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
    const detail = await res.json().catch(() => ({}));
    throw new ApiError(res.status, detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const api = {
  async register(username: string, password: string) {
    return request("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
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
  async health() {
    return request<{ documents: number; auth_enabled: boolean; llm: string }>("/healthz");
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

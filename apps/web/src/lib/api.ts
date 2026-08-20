/**
 * The one place a request is shaped and a token is attached.
 *
 * The browser reaches the API directly rather than through a Next.js proxy:
 * one origin to reason about, one place the token lives, and no server-side
 * copy of a credential that belongs to the person holding the tab.
 */

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "plimsoll.token";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export function readToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function storeToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export function apiBase(): string {
  return BASE;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = readToken();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });

  if (response.status === 401) {
    // The access token has a short life and this client does not yet rotate
    // its refresh token, so an expired session becomes a sign-in rather than a
    // page of failures the user cannot act on.
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/";
    throw new ApiError(401, "UNAUTHENTICATED", "The session has expired.");
  }

  if (!response.ok) {
    // The API answers a documented error envelope; surfacing its message is
    // what makes a refusal actionable rather than a status code.
    let code = "UNKNOWN";
    let message = response.statusText;
    try {
      const body = (await response.json()) as { code?: string; message?: string };
      code = body.code ?? code;
      message = body.message ?? message;
    } catch {
      // A response with no envelope: the status is all there is to report.
    }
    throw new ApiError(response.status, code, message);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
};

export async function signIn(email: string, password: string): Promise<void> {
  const body = await request<{ accessToken: string }>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  storeToken(body.accessToken);
}

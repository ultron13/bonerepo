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
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
  }

  /**
   * The failing checks, when the refusal carried any.
   *
   * Preflight reports every failure at once rather than the first, and that
   * list is the whole reason the endpoint is shaped the way it is. Showing
   * only the summary would send someone round the loop once per problem.
   */
  checks(): string[] {
    const checks = this.details?.checks;
    if (!Array.isArray(checks)) return [];
    return checks
      .filter((check) => (check as { status?: string }).status === "FAIL")
      .map((check) => {
        const { code, message } = check as { code?: string; message?: string };
        return message ? `${code}: ${message}` : String(code);
      });
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

/**
 * `signingIn` marks the one request whose 401 is an answer rather than an
 * expiry. Everything else that gets a 401 has presented a token that is no
 * longer good, and the session ends; sign-in has presented a password that
 * was wrong, and the person needs to be told so rather than returned to a
 * blank form.
 */
async function request<T>(
  path: string,
  init: RequestInit = {},
  signingIn = false,
): Promise<T> {
  const token = readToken();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });

  if (response.status === 401 && !signingIn) {
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
    let details: Record<string, unknown> | undefined;
    try {
      const body = (await response.json()) as {
        error?: {
          code?: string;
          message?: string;
          details?: Record<string, unknown>;
        };
      };
      const envelope = body.error ?? {};
      code = envelope.code ?? code;
      message = envelope.message ?? message;
      details = envelope.details;
    } catch {
      // A response with no envelope: the status is all there is to report.
    }
    throw new ApiError(response.status, code, message, details);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
};

export async function signIn(email: string, password: string): Promise<void> {
  const body = await request<{ accessToken: string }>(
    "/api/v1/auth/login",
    { method: "POST", body: JSON.stringify({ email, password }) },
    true,
  );
  storeToken(body.accessToken);
}

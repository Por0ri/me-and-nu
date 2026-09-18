export type User = {
  id: number;
  email: string;
  nickname: string;
  role: "consumer" | "creator" | "admin";
};

export type RegisterInput = {
  email: string;
  password: string;
  nickname: string;
  role: "consumer" | "creator";
};

export type LoginInput = {
  email: string;
  password: string;
};

export type LoginResponse = {
  message: string;
  user: User;
};

export type MessageResponse = {
  message: string;
};

type ErrorPayload = {
  detail?: string | Array<{ msg?: string }>;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const apiBaseUrl = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

function getErrorMessage(payload: ErrorPayload | null, status: number): string {
  if (typeof payload?.detail === "string") {
    return payload.detail;
  }

  if (Array.isArray(payload?.detail)) {
    return payload.detail
      .map((item) => item.msg)
      .filter(Boolean)
      .join(", ");
  }

  return `요청에 실패했습니다. (HTTP ${status})`;
}

async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });

  const payload = (await response.json().catch(() => null)) as
    | T
    | ErrorPayload
    | null;

  if (!response.ok) {
    throw new ApiError(
      getErrorMessage(payload as ErrorPayload | null, response.status),
      response.status,
    );
  }

  return payload as T;
}

export function register(input: RegisterInput): Promise<User> {
  return apiRequest<User>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function login(input: LoginInput): Promise<LoginResponse> {
  return apiRequest<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getCurrentUser(): Promise<User> {
  return apiRequest<User>("/api/v1/auth/me");
}

export function logout(): Promise<MessageResponse> {
  return apiRequest<MessageResponse>("/api/v1/auth/logout", {
    method: "POST",
  });
}

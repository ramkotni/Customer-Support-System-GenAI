import { API_BASE, authHeaders } from './config';

/** The backend's error envelope: `{ error: { code, message, details, request_id } }`. */
export interface ApiErrorBody {
  code: string;
  message: string;
  details?: unknown;
  request_id?: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    super(body?.message || fallback);
    this.name = 'ApiError';
    this.status = status;
    this.code = body?.code ?? 'http_error';
    this.requestId = body?.request_id ?? null;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody | null = null;
  try {
    const json = await response.json();
    if (json && typeof json === 'object' && 'error' in json) {
      body = (json as { error: ApiErrorBody }).error;
    }
  } catch {
    /* a non-JSON error body (proxy error page, etc.) */
  }
  return new ApiError(response.status, body, `Request failed (${response.status}).`);
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
}

/** JSON request against the API. Throws `ApiError` on a non-2xx response. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = options;

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    signal,
    headers: {
      ...authHeaders(),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** POST that returns a readable SSE stream instead of JSON. */
export async function openEventStream(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    signal,
    headers: {
      ...authHeaders(),
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
  });

  // Errors arrive before the stream opens (auth, ownership, 404, validation).
  if (!response.ok) throw await parseError(response);
  if (!response.body) throw new ApiError(500, null, 'The response had no body to stream.');
  return response;
}

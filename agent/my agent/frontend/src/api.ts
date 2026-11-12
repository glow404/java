/** 同源 HTTP 客户端：Cookie 由浏览器管理，写入请求携带服务端 CSRF 令牌。 */
let csrfToken = '';
export function setCsrfToken(token: string) { csrfToken = token; }

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError'; }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', csrfToken);
  const response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && !['/auth/me', '/auth/login'].includes(path)) {
      window.dispatchEvent(new Event('vibe:session-expired'));
    }
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `请求失败（${response.status}）`;
    throw new ApiError(message, response.status);
  }
  return body as T;
}
export const jsonBody = (value: unknown) => JSON.stringify(value);
export const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
export const downloadUrl = (path: string) => `/api${path}`;

/**
 * REST 封装（Plan §8 lib/api.ts）：
 *   * token 存 localStorage，请求自动带 Authorization: Bearer <token>；
 *   * 带 token 的请求收到 401 → 清 token 并跳登录页（登录接口本身不带 token，不受影响）；
 *   * 非 2xx 一律抛 ApiError（code 取后端统一错误码，message 可直接展示）。
 *
 * 接口基地址：
 *   * 开发期 next dev 读 NEXT_PUBLIC_API_BASE（.env.development，指向 127.0.0.1:8000）；
 *   * 发布期静态导出由 FastAPI 同域托管，默认走相对路径 /api/v1。
 */

const TOKEN_KEY = "ia_token";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

export class ApiError extends Error {
  /** 后端统一错误码（40000/40101/...），网络层失败为 0 */
  readonly code: number;
  /** HTTP 状态码 */
  readonly status: number;
  readonly detail?: unknown;

  constructor(message: string, code: number, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export interface ApiOptions {
  method?: string;
  /** JSON 请求体（自动 JSON 序列化并带 Content-Type） */
  json?: unknown;
}

export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.json !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: opts.method ?? (opts.json !== undefined ? "POST" : "GET"),
      headers,
      body: opts.json !== undefined ? JSON.stringify(opts.json) : undefined,
    });
  } catch (err) {
    throw new ApiError(
      "网络错误：无法连接服务器，请检查后端是否已启动",
      0,
      0,
      String(err),
    );
  }

  return _handleResponse<T>(res, token);
}

/**
 * multipart 上传（POST FormData）。不带 Content-Type，让浏览器自动设 boundary。
 */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { method: "POST", headers, body: form });
  } catch (err) {
    throw new ApiError(
      "网络错误：无法连接服务器，请检查后端是否已启动",
      0,
      0,
      String(err),
    );
  }
  return _handleResponse<T>(res, token);
}

async function _handleResponse<T>(res: Response, token: string | null): Promise<T> {
  // 204 无 body（删除等接口）
  if (res.status === 204) return undefined as T;

  // 空 body 容错（理论上本项目的接口都有 JSON 响应）
  const body: unknown = await res.json().catch(() => null);

  // 带 token 却 401：登录态失效，清掉并回登录页
  if (res.status === 401 && token) {
    setToken(null);
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
  }

  if (!res.ok) {
    const err = (body ?? {}) as { code?: number; message?: string; detail?: unknown };
    throw new ApiError(
      err.message ?? `请求失败（HTTP ${res.status}）`,
      err.code ?? 0,
      res.status,
      err.detail,
    );
  }
  return body as T;
}

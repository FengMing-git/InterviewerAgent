/**
 * 与后端 backend/app/models/schemas.py 保持一致的数据契约（手工同步，P0 只有认证/用户部分）。
 * 后端错误统一格式：{ code: number, message: string, detail?: unknown }
 */

export interface TokenResponse {
  token: string;
  /** 有效期（秒） */
  expires_in: number;
}

export interface RegisterRequest {
  username: string;
  password: string;
  display_name?: string | null;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface UserProfileUpdate {
  display_name?: string;
  target_position?: string;
}

export interface UserOut {
  id: number;
  username: string;
  display_name: string | null;
  target_position: string | null;
  has_custom_llm: boolean;
  quota_date: string | null;
  quota_used: number;
}

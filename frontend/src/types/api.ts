/**
 * 与后端 backend/app/models/schemas.py 保持一致的数据契约（手工同步）。
 * 后端错误统一格式：{ code: number, message: string, detail?: unknown }
 */

// --------------------------------------------------------------------------- //
// 面试准备流程（P1：D1 画像 / D2 项目分析报告 / 面试接口）
// --------------------------------------------------------------------------- //

export type TaskStatus = "pending" | "running" | "done" | "partial" | "failed";

export type InterviewStatus =
  | "preparing"
  | "awaiting_confirm"
  | "initializing"
  | "ready"
  | "queued"
  | "running"
  | "finished"
  | "aborted";

export interface BasicInfo {
  name?: string | null;
  school?: string | null;
  major?: string | null;
  degree?: string | null;
  /** 毕业年月，保留原文精度（YYYY-MM 或年份） */
  grad_date?: string | null;
}

export interface ResumeProject {
  id: string;
  name: string;
  role?: string | null;
  period?: string | null;
  tech_stack: string[];
  description?: string | null;
  repo_url?: string | null;
}

export interface Award {
  name: string;
  level?: string | null;
  date?: string | null;
}

export interface Internship {
  company?: string | null;
  position?: string | null;
  period?: string | null;
  description?: string | null;
}

/** D1 简历结构化画像 */
export interface ResumeProfile {
  basic: BasicInfo;
  skills: string[];
  projects: ResumeProject[];
  awards: Award[];
  internships: Internship[];
}

export interface RepoMeta {
  url: string;
  stars?: number | null;
  forks?: number | null;
  /** 各语言占比（百分比，和≈100） */
  language_ratio?: Record<string, number> | null;
  last_commit_at?: string | null;
}

export interface KeyModule {
  name: string;
  path?: string | null;
  summary?: string | null;
}

export interface Finding {
  /** 证据编号 F-001..（后端统一重排） */
  id: string;
  text: string;
  source_url: string;
}

/** D2 项目分析报告（每仓库 1 份） */
export interface ProjectReport {
  repo: RepoMeta;
  readme_summary?: string | null;
  tech_stack: string[];
  key_modules: KeyModule[];
  findings: Finding[];
  related_project_id?: string | null;
}

export interface InterviewConfig {
  style: "严谨" | "温和" | "犀利";
  difficulty: 1 | 2 | 3;
  position: string;
}

export interface WarningItem {
  task: string;
  message: string;
  code?: number;
  repo?: string;
}

export interface InterviewSummary {
  id: number;
  status: InterviewStatus;
  stage: number;
  position?: string | null;
  total_score?: number | null;
  created_at: string;
}

export interface InterviewDetail {
  id: number;
  status: InterviewStatus;
  stage: number;
  parse_status: TaskStatus;
  analysis_status: TaskStatus;
  research_status: TaskStatus;
  init_status: TaskStatus;
  report_status: TaskStatus;
  repo_urls: string[] | null;
  profile: ResumeProfile | null;
  project_reports: ProjectReport[] | null;
  verification_items: unknown[] | null;
  persona: Record<string, unknown> | null;
  directions: unknown[] | null;
  config: InterviewConfig | null;
  warnings: WarningItem[] | null;
  total_score?: number | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface InterviewCreated {
  id: number;
  status: InterviewStatus;
}

export interface ProfileConfirmRequest {
  profile: ResumeProfile;
  config: InterviewConfig;
}

export interface RetryAccepted {
  id: number;
  task: string;
  status: InterviewStatus;
}

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

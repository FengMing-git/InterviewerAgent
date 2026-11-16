"use client";

/**
 * 准备页（Plan §8 /prepare?id=，三步向导）：
 *   第 1 步：轮询解析 / 项目分析任务状态（失败可重试），完成后展示项目分析报告；
 *   第 2 步：编辑画像（D1）与面试配置，确认后提交（PUT /profile）；
 *   第 3 步：面试官初始化与联网核验（P2 里程碑开放），当前显示占位说明。
 *
 * 静态导出无服务端路由：id 从查询参数读取（useSearchParams + Suspense）。
 */

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { InterviewStatusBadge, TaskStatusBadge } from "@/components/status-badge";
import { TagInput } from "@/components/tag-input";
import { api, getToken } from "@/lib/api";
import type {
  InterviewConfig,
  InterviewDetail,
  ProjectReport,
  ResumeProfile,
  TaskStatus,
} from "@/types/api";

const POLL_INTERVAL_MS = 2000;

export default function PreparePage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-12 text-sm text-muted-foreground">
          正在加载……
        </main>
      }
    >
      <PrepareWizard />
    </Suspense>
  );
}

function needsPoll(detail: InterviewDetail | null): boolean {
  if (!detail || detail.status !== "preparing") return false;
  const busy = (s: TaskStatus) => s === "pending" || s === "running";
  return busy(detail.parse_status) || busy(detail.analysis_status);
}

function PrepareWizard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const id = searchParams.get("id");

  const [detail, setDetail] = useState<InterviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setDetail(await api<InterviewDetail>(`/interviews/${id}`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [id]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    if (!id) return; // 缺参提示在渲染层直接给出（避免 effect 内同步 setState）
    // setTimeout 把首拉取踢出 effect 同步段（react-hooks/set-state-in-effect）
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [id, load, router]);

  // 任务进行中每 2 s 轮询（向导页轮询 GET /interviews/{id}）
  const polling = needsPoll(detail);
  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [polling, load]);

  async function retry(task: string) {
    if (!id) return;
    setRetrying(task);
    try {
      await api(`/interviews/${id}/retry/${task}`, { method: "POST" });
      // 乐观置为 running 以恢复轮询；下一轮轮询会校正
      setDetail((prev) =>
        prev ? { ...prev, [`${task}_status`]: "running" as TaskStatus } : prev,
      );
      void load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重试失败");
    } finally {
      setRetrying(null);
    }
  }

  if (!id) {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-12">
        <p className="text-sm text-destructive">缺少面试 id 参数</p>
        <Button variant="outline" size="sm" className="mt-4" asChild>
          <Link href="/">返回首页</Link>
        </Button>
      </main>
    );
  }

  if (error && !detail) {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-12">
        <p className="text-sm text-destructive">{error}</p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.push("/")}>
          返回首页
        </Button>
      </main>
    );
  }
  if (!detail) {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-12 text-sm text-muted-foreground">
        加载中……
      </main>
    );
  }

  const step = detail.status === "preparing" ? 1 : detail.status === "awaiting_confirm" ? 2 : 3;

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10">
      <header className="mb-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">面试准备</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              面试 #{detail.id} · <InterviewStatusBadge status={detail.status} />
            </p>
          </div>
          <Button variant="outline" size="sm" asChild>
            <Link href="/">返回首页</Link>
          </Button>
        </div>
      </header>

      <StepIndicator current={step} />

      <div className="mt-6 space-y-6">
        <TaskPanel detail={detail} retrying={retrying} onRetry={retry} />

        <ProjectReportsSection detail={detail} />

        {step >= 2 && detail.profile ? (
          <ProfileEditor
            key={detail.status === "awaiting_confirm" ? "editing" : "confirmed"}
            detail={detail}
            onConfirmed={setDetail}
          />
        ) : null}

        {error ? <p className="text-sm text-destructive">{error}</p> : null}
      </div>
    </main>
  );
}

/* -------------------------------------------------------------------------- */
/* 步骤指示器                                                                  */
/* -------------------------------------------------------------------------- */
const STEPS = ["上传与解析", "确认画像", "开始面试"];

function StepIndicator({ current }: { current: number }) {
  return (
    <ol className="flex items-center gap-2 text-sm">
      {STEPS.map((label, i) => {
        const index = i + 1;
        const state = index < current ? "done" : index === current ? "active" : "todo";
        return (
          <li key={label} className="flex flex-1 items-center gap-2">
            <span
              className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${
                state === "done"
                  ? "bg-primary text-primary-foreground"
                  : state === "active"
                    ? "bg-primary/15 text-primary ring-1 ring-primary/40"
                    : "bg-muted text-muted-foreground"
              }`}
            >
              {state === "done" ? "✓" : index}
            </span>
            <span className={state === "todo" ? "text-muted-foreground" : ""}>{label}</span>
            {i < STEPS.length - 1 && <span className="h-px flex-1 bg-border" />}
          </li>
        );
      })}
    </ol>
  );
}

/* -------------------------------------------------------------------------- */
/* 第 1 步：任务状态面板                                                       */
/* -------------------------------------------------------------------------- */
const TASK_LABELS: Record<string, string> = {
  parse: "简历解析",
  analysis: "项目分析",
  research: "联网核验",
  init: "面试官初始化",
  report: "评估报告",
};

function TaskPanel({
  detail,
  retrying,
  onRetry,
}: {
  detail: InterviewDetail;
  retrying: string | null;
  onRetry: (task: string) => void;
}) {
  const tasks: Array<{ key: string; status: TaskStatus }> = [
    { key: "parse", status: detail.parse_status },
    { key: "analysis", status: detail.analysis_status },
  ];
  const warnings = detail.warnings ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>解析进度</CardTitle>
        <CardDescription>
          简历解析与项目分析并行执行，完成后进入第 2 步确认画像
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="space-y-2">
          {tasks.map(({ key, status }) => (
            <li key={key} className="flex items-center justify-between gap-3 rounded-lg border p-3">
              <span className="text-sm font-medium">{TASK_LABELS[key]}</span>
              <div className="flex items-center gap-2">
                <TaskStatusBadge status={status} />
                {status === "failed" && (
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={retrying === key}
                    onClick={() => onRetry(key)}
                  >
                    {retrying === key ? "重试中……" : "重试"}
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>

        {warnings.length > 0 && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
            <p className="font-medium text-amber-600 dark:text-amber-400">提示信息</p>
            <ul className="mt-1.5 list-disc space-y-1 pl-5 text-muted-foreground">
              {warnings.map((w, i) => (
                <li key={i}>
                  {w.repo ? <span className="font-mono text-xs">{w.repo}：</span> : null}
                  {w.message}
                </li>
              ))}
            </ul>
          </div>
        )}

        {detail.repo_urls && detail.repo_urls.length > 0 && (
          <div className="flex flex-wrap gap-1.5 text-xs">
            {detail.repo_urls.map((url) => (
              <a
                key={url}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-md bg-muted px-2 py-0.5 font-mono text-muted-foreground hover:text-primary"
              >
                {url.replace("https://github.com/", "")}
              </a>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* 项目分析报告（D2）展示                                                      */
/* -------------------------------------------------------------------------- */
function ProjectReportsSection({ detail }: { detail: InterviewDetail }) {
  const reports = detail.project_reports;
  if (!reports?.length) return null;
  const ready =
    detail.analysis_status === "done" || detail.analysis_status === "partial";
  if (!ready) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>项目分析报告</CardTitle>
        <CardDescription>
          由 GitHub 公开数据与代码分析生成，作为面试提问的证据来源
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {reports.map((report, index) => (
          <RepoReportCard key={report.repo.url} report={report} index={index} />
        ))}
      </CardContent>
    </Card>
  );
}

function RepoReportCard({
  report,
  index,
}: {
  report: ProjectReport;
  index: number;
}) {
  const repoName = report.repo.url.replace("https://github.com/", "");
  const languages = Object.entries(report.repo.language_ratio ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <section className="rounded-xl border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant="secondary">仓库 {index + 1}</Badge>
          <a
            href={report.repo.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-sm font-medium hover:text-primary"
          >
            {repoName}
          </a>
          {report.related_project_id && (
            <Badge variant="outline">关联画像项目 {report.related_project_id}</Badge>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span>★ {report.repo.stars ?? "-"}</span>
          <span>fork {report.repo.forks ?? "-"}</span>
          {report.repo.last_commit_at && (
            <span>最近推送 {report.repo.last_commit_at.slice(0, 10)}</span>
          )}
        </div>
      </div>

      {languages.length > 0 && (
        <div className="mt-3 space-y-1">
          {languages.map(([lang, pct]) => (
            <div key={lang} className="flex items-center gap-2 text-xs">
              <span className="w-20 shrink-0 text-muted-foreground">{lang}</span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary/70"
                  style={{ width: `${Math.min(100, pct)}%` }}
                />
              </div>
              <span className="w-12 text-right tabular-nums text-muted-foreground">{pct}%</span>
            </div>
          ))}
        </div>
      )}

      {report.readme_summary && (
        <p className="mt-3 text-sm leading-relaxed text-foreground/90">{report.readme_summary}</p>
      )}

      {report.tech_stack.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {report.tech_stack.map((tech) => (
            <Badge key={tech} variant="muted">
              {tech}
            </Badge>
          ))}
        </div>
      )}

      {report.key_modules.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-muted-foreground">核心模块</p>
          <ul className="mt-1.5 space-y-1 text-sm">
            {report.key_modules.map((m) => (
              <li key={`${m.name}-${m.path ?? ""}`} className="flex flex-wrap gap-x-2">
                <span className="font-medium">{m.name}</span>
                {m.path && <span className="font-mono text-xs text-muted-foreground">{m.path}</span>}
                {m.summary && <span className="text-muted-foreground">— {m.summary}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.findings.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-muted-foreground">可核验事实（面试提问证据）</p>
          <ul className="mt-1.5 space-y-1.5 text-sm">
            {report.findings.map((f) => (
              <li key={f.id} className="flex items-start gap-2">
                <Badge variant="info" className="mt-0.5 shrink-0 font-mono text-[10px]">
                  {f.id}
                </Badge>
                <span className="flex-1">{f.text}</span>
                <a
                  href={f.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="shrink-0 text-xs text-muted-foreground underline-offset-2 hover:text-primary hover:underline"
                >
                  来源
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* 第 2 步：画像编辑 + 面试配置                                                */
/* -------------------------------------------------------------------------- */
function ProfileEditor({
  detail,
  onConfirmed,
}: {
  detail: InterviewDetail;
  onConfirmed: (detail: InterviewDetail) => void;
}) {
  const [profile, setProfile] = useState<ResumeProfile>(() =>
    JSON.parse(JSON.stringify(detail.profile)),
  );
  const [config, setConfig] = useState<InterviewConfig>(() => ({
    style: detail.config?.style ?? "严谨",
    difficulty: detail.config?.difficulty ?? 2,
    position: detail.config?.position ?? "后端",
  }));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirmed = detail.status !== "awaiting_confirm";

  function updateBasic(field: keyof ResumeProfile["basic"], value: string) {
    setProfile((prev) => ({ ...prev, basic: { ...prev.basic, [field]: value || null } }));
  }

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      const updated = await api<InterviewDetail>(`/interviews/${detail.id}/profile`, {
        method: "PUT",
        json: { profile, config },
      });
      onConfirmed(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>确认画像与面试配置</CardTitle>
        <CardDescription>
          解析结果可能有偏差，请核对并修正；确认后将进入面试官初始化
          {confirmed ? "（已确认，以下为只读展示）" : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-8">
        {/* 基本信息 */}
        <section className="space-y-3">
          <h3 className="text-sm font-medium">基本信息</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <LabeledInput label="姓名" value={profile.basic.name ?? ""} disabled={confirmed}
              onChange={(v) => updateBasic("name", v)} />
            <LabeledInput label="学校" value={profile.basic.school ?? ""} disabled={confirmed}
              onChange={(v) => updateBasic("school", v)} />
            <LabeledInput label="专业" value={profile.basic.major ?? ""} disabled={confirmed}
              onChange={(v) => updateBasic("major", v)} />
            <LabeledInput label="学历" value={profile.basic.degree ?? ""} disabled={confirmed}
              onChange={(v) => updateBasic("degree", v)} />
            <LabeledInput label="毕业时间" value={profile.basic.grad_date ?? ""} disabled={confirmed}
              placeholder="如 2027-06" onChange={(v) => updateBasic("grad_date", v)} />
          </div>
        </section>

        {/* 技能 */}
        <section className="space-y-2">
          <h3 className="text-sm font-medium">技能</h3>
          <TagInput
            value={profile.skills}
            disabled={confirmed}
            placeholder="如 Python、MySQL，回车添加"
            onChange={(skills) => setProfile((prev) => ({ ...prev, skills }))}
          />
        </section>

        {/* 项目 */}
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">项目经历</h3>
            {!confirmed && (
              <Button variant="outline" size="xs"
                onClick={() =>
                  setProfile((prev) => ({
                    ...prev,
                    projects: [
                      ...prev.projects,
                      { id: "", name: "新项目", tech_stack: [] },
                    ],
                  }))
                }
              >
                + 添加项目
              </Button>
            )}
          </div>
          <div className="space-y-4">
            {profile.projects.map((project, index) => (
              <div key={index} className="space-y-3 rounded-xl border p-4">
                <div className="flex items-center justify-between gap-2">
                  <Badge variant="secondary">{project.id || `P${index + 1}`}</Badge>
                  {!confirmed && profile.projects.length > 1 && (
                    <Button variant="ghost" size="xs" className="text-destructive"
                      onClick={() =>
                        setProfile((prev) => ({
                          ...prev,
                          projects: prev.projects.filter((_, i) => i !== index),
                        }))
                      }
                    >
                      删除项目
                    </Button>
                  )}
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <LabeledInput label="项目名称" value={project.name} disabled={confirmed}
                    onChange={(v) => updateProject(index, "name", v)} />
                  <LabeledInput label="担任角色" value={project.role ?? ""} disabled={confirmed}
                    onChange={(v) => updateProject(index, "role", v)} />
                  <LabeledInput label="起止时间" value={project.period ?? ""} disabled={confirmed}
                    placeholder="如 2026.03-2026.09"
                    onChange={(v) => updateProject(index, "period", v)} />
                  <LabeledInput label="仓库地址" value={project.repo_url ?? ""} disabled={confirmed}
                    placeholder="https://github.com/…" inputMode="url"
                    onChange={(v) => updateProject(index, "repo_url", v)} />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">技术栈</Label>
                  <TagInput
                    value={project.tech_stack}
                    disabled={confirmed}
                    onChange={(tech) => updateProject(index, "tech_stack", tech)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">项目描述</Label>
                  <Textarea
                    rows={2}
                    disabled={confirmed}
                    value={project.description ?? ""}
                    onChange={(e) => updateProject(index, "description", e.target.value)}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* 奖项与实习（紧凑展示，可编辑） */}
        <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <SimpleListSection
            title="获奖情况"
            disabled={confirmed}
            items={profile.awards.map((a) => [a.name, a.level ?? "", a.date ?? ""])}
            headers={["奖项", "级别", "时间"]}
            onChange={(rows) =>
              setProfile((prev) => ({
                ...prev,
                awards: rows.map(([name, level, date]) => ({ name, level, date })),
              }))
            }
          />
          <SimpleListSection
            title="实习经历"
            disabled={confirmed}
            items={profile.internships.map((i) => [i.company ?? "", i.position ?? "", i.period ?? "", i.description ?? ""])}
            headers={["公司", "岗位", "时间", "内容"]}
            onChange={(rows) =>
              setProfile((prev) => ({
                ...prev,
                internships: rows.map(([company, position, period, description]) => ({
                  company,
                  position,
                  period,
                  description,
                })),
              }))
            }
          />
        </section>

        {/* 面试配置（SRS I4） */}
        <section className="space-y-3">
          <h3 className="text-sm font-medium">面试配置</h3>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">面试官风格</Label>
              <select
                disabled={confirmed}
                value={config.style}
                onChange={(e) => setConfig({ ...config, style: e.target.value as InterviewConfig["style"] })}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring dark:bg-input/30"
              >
                <option value="严谨">严谨</option>
                <option value="温和">温和</option>
                <option value="犀利">犀利</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">难度</Label>
              <div className="flex h-8 overflow-hidden rounded-lg border border-input text-sm">
                {([1, 2, 3] as const).map((level) => (
                  <button
                    key={level}
                    type="button"
                    disabled={confirmed}
                    className={`flex-1 transition-colors ${
                      config.difficulty === level
                        ? "bg-primary text-primary-foreground"
                        : "hover:bg-muted"
                    }`}
                    onClick={() => setConfig({ ...config, difficulty: level })}
                  >
                    {level === 1 ? "基础" : level === 2 ? "进阶" : "挑战"}
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">目标岗位</Label>
              <select
                disabled={confirmed}
                value={config.position}
                onChange={(e) => setConfig({ ...config, position: e.target.value })}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring dark:bg-input/30"
              >
                <option value="后端">后端</option>
                <option value="前端">前端</option>
                <option value="算法">算法</option>
              </select>
            </div>
          </div>
        </section>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}
      </CardContent>
      {!confirmed ? (
        <CardContent className="pt-0">
          <Button className="w-full" disabled={submitting} onClick={handleConfirm}>
            {submitting ? "正在确认……" : "确认画像，开始初始化"}
          </Button>
        </CardContent>
      ) : (
        <CardContent className="pt-0">
          <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
            画像已确认并保存。面试官初始化与联网核验将在下一个版本开放
            （当前任务状态：初始化 {detail.init_status === "pending" ? "待启动" : detail.init_status}
            、核验 {detail.research_status === "pending" ? "待启动" : detail.research_status}）。
          </div>
        </CardContent>
      )}
    </Card>
  );

  function updateProject(index: number, field: string, value: unknown) {
    setProfile((prev) => {
      const projects = prev.projects.map((p, i) =>
        i === index ? { ...p, [field]: value } : p,
      );
      return { ...prev, projects };
    });
  }
}

/* -------------------------------------------------------------------------- */
/* 小件：带标签的输入框 / 简单行编辑器                                          */
/* -------------------------------------------------------------------------- */
function LabeledInput({
  label,
  value,
  onChange,
  placeholder,
  disabled,
  inputMode,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  inputMode?: "url" | "text";
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <Input
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        inputMode={inputMode}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function SimpleListSection({
  title,
  headers,
  items,
  onChange,
  disabled,
}: {
  title: string;
  headers: string[];
  items: string[][];
  onChange: (rows: string[][]) => void;
  disabled?: boolean;
}) {
  const columns = headers.length;
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{title}</h3>
        {!disabled && (
          <Button variant="outline" size="xs"
            onClick={() => onChange([...items, new Array(columns).fill("")])}
          >
            + 添加
          </Button>
        )}
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-muted-foreground">（无）</p>
      ) : (
        <div className="space-y-2">
          {items.map((row, rowIndex) => (
            <div key={rowIndex} className="flex items-end gap-1.5">
              {headers.map((header, colIndex) => (
                <div key={header} className={colIndex === columns - 1 ? "flex-1" : "w-24 shrink-0"}>
                  <Input
                    value={row[colIndex] ?? ""}
                    disabled={disabled}
                    placeholder={header}
                    className="h-7 text-xs"
                    onChange={(e) => {
                      const next = items.map((r, i) =>
                        i === rowIndex ? r.map((c, j) => (j === colIndex ? e.target.value : c)) : r,
                      );
                      onChange(next);
                    }}
                  />
                </div>
              ))}
              {!disabled && (
                <Button variant="ghost" size="icon-sm" aria-label="删除该行" className="text-destructive"
                  onClick={() => onChange(items.filter((_, i) => i !== rowIndex))}
                >
                  ×
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

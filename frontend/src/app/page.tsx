"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { InterviewStatusBadge } from "@/components/status-badge";
import { api, getToken, setToken } from "@/lib/api";
import type { InterviewSummary } from "@/types/api";

/**
 * 首页：历史面试列表（状态 / 得分 / 时间）+ 新建入口。
 * 点击未结束的面试进入准备页；已结束的进入报告页（P4 里程碑开放）。
 */
export default function HomePage() {
  const router = useRouter();
  const [interviews, setInterviews] = useState<InterviewSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setInterviews(await api<InterviewSummary[]>("/interviews"));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    // setTimeout 把首拉取踢出 effect 同步段（react-hooks/set-state-in-effect）
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load, router]);

  async function handleDelete(id: number) {
    if (!window.confirm(`确定删除面试 #${id} 吗？对话记录与简历原件将一并删除。`)) return;
    try {
      await api(`/interviews/${id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  }

  function handleLogout() {
    setToken(null);
    router.replace("/login");
  }

  function hrefFor(interview: InterviewSummary): string {
    return interview.status === "finished" || interview.status === "aborted"
      ? `/report?id=${interview.id}`
      : `/prepare?id=${interview.id}`;
  }

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-12">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">InterviewerAgent</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            简历解析 → 联网核实 → 模拟面试 → 评估报告
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild>
            <Link href="/new">新建面试</Link>
          </Button>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            退出登录
          </Button>
        </div>
      </header>

      <section className="mt-10 space-y-4">
        <h2 className="font-medium">历史面试</h2>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {!error && interviews === null ? (
          <p className="text-sm text-muted-foreground">加载中……</p>
        ) : null}

        {interviews && interviews.length === 0 ? (
          <div className="rounded-lg border border-dashed p-8 text-center">
            <p className="text-sm text-muted-foreground">
              还没有面试记录。上传一份简历，开始第一场有据可依的模拟面试吧。
            </p>
            <Button asChild className="mt-4">
              <Link href="/new">新建面试</Link>
            </Button>
          </div>
        ) : null}

        {interviews && interviews.length > 0 ? (
          <ul className="divide-y rounded-lg border">
            {interviews.map((interview) => (
              <li key={interview.id} className="flex items-center gap-3 px-4 py-3">
                <Link
                  href={hrefFor(interview)}
                  className="flex flex-1 items-center gap-3 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                >
                  <span className="text-sm tabular-nums text-muted-foreground">
                    #{interview.id}
                  </span>
                  <InterviewStatusBadge status={interview.status} />
                  {interview.position ? (
                    <span className="text-sm text-muted-foreground">{interview.position}</span>
                  ) : null}
                  {interview.total_score != null && (
                    <span className="text-sm font-medium tabular-nums">
                      {Number(interview.total_score).toFixed(1)} 分
                    </span>
                  )}
                  <span className="ml-auto text-xs text-muted-foreground">
                    {new Date(interview.created_at).toLocaleString("zh-CN", { hour12: false })}
                  </span>
                </Link>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`删除面试 ${interview.id}`}
                  className="text-destructive"
                  onClick={() => void handleDelete(interview.id)}
                >
                  ×
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </main>
  );
}

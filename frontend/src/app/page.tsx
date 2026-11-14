"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { api, getToken, setToken } from "@/lib/api";
import type { UserOut } from "@/types/api";

/**
 * 首页：P0 只做登录态展示 + 登出闭环；历史面试列表与「新建面试」入口随 P1 落地。
 */
export default function HomePage() {
  const router = useRouter();
  const [user, setUser] = useState<UserOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    api<UserOut>("/users/me")
      .then(setUser)
      .catch((err: unknown) => {
        // api() 在 401 时已清 token 并跳登录页，这里处理其余错误
        setError(err instanceof Error ? err.message : "加载用户信息失败");
      });
  }, [router]);

  function handleLogout() {
    setToken(null);
    router.replace("/login");
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
        {user ? (
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">
              你好，{user.display_name ?? user.username}
            </span>
            <Button variant="outline" size="sm" onClick={handleLogout}>
              退出登录
            </Button>
          </div>
        ) : null}
      </header>

      <section className="mt-12 space-y-4">
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {!error && !user ? <p className="text-sm text-muted-foreground">加载中……</p> : null}
        {user ? (
          <div className="rounded-lg border border-dashed p-6">
            <h2 className="font-medium">历史面试</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              还没有面试记录——「新建面试」功能将在下一个里程碑（P1 简历解析）开放。
            </p>
          </div>
        ) : null}
        <p className="text-sm text-muted-foreground">
          需要修改个人资料？{" "}
          <Link href="/settings" className="text-primary underline-offset-4 hover:underline">
            设置页（P5 里程碑）
          </Link>
        </p>
      </section>
    </main>
  );
}

"use client";

/**
 * 新建面试（Plan §8 /new，准备三步中的第 1 步）：
 * 上传简历（pdf/docx/txt/md，≤10MB）+ 1~3 个 GitHub 公开仓库地址 + 隐私告知勾选。
 * 提交成功跳 /prepare?id= 轮询解析进度。
 */

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiUpload } from "@/lib/api";
import type { InterviewCreated } from "@/types/api";

const ACCEPT = ".pdf,.docx,.txt,.md";
const MAX_REPOS = 3;

export default function NewInterviewPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [repos, setRepos] = useState<string[]>([""]);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const validRepos = repos.map((r) => r.trim()).filter(Boolean);
  const canSubmit = Boolean(file) && validRepos.length >= 1 && consent && !submitting;

  function addRepo() {
    if (repos.length < MAX_REPOS) setRepos([...repos, ""]);
  }

  function removeRepo(index: number) {
    const next = repos.filter((_, i) => i !== index);
    setRepos(next.length ? next : [""]);
  }

  function setRepo(index: number, value: string) {
    setRepos(repos.map((r, i) => (i === index ? value : r)));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      const form = new FormData();
      form.append("resume", file);
      for (const repo of validRepos) form.append("repo_urls", repo);
      const created = await apiUpload<InterviewCreated>("/interviews", form);
      router.push(`/prepare?id=${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">新建面试</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          第 1 步 / 共 3 步：上传简历并提交仓库地址，随后确认画像
        </p>
      </header>

      <form onSubmit={handleSubmit}>
        <Card>
          <CardHeader>
            <CardTitle>简历与项目</CardTitle>
            <CardDescription>
              支持 PDF / DOCX / TXT / MD，不超过 10 MB；仓库须为 GitHub 公开仓库
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* 简历上传 */}
            <div className="space-y-2">
              <Label htmlFor="resume">简历文件</Label>
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className="flex w-full flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-input px-4 py-8 text-center transition-colors hover:border-ring hover:bg-muted/50"
              >
                <span className="text-sm font-medium">
                  {file ? file.name : "点击选择简历文件"}
                </span>
                <span className="text-xs text-muted-foreground">
                  {file
                    ? `${(file.size / 1024).toFixed(1)} KB`
                    : "PDF / DOCX / TXT / MD，≤ 10 MB"}
                </span>
              </button>
              <input
                ref={fileRef}
                id="resume"
                type="file"
                accept={ACCEPT}
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>

            {/* 仓库地址 */}
            <div className="space-y-2">
              <Label>GitHub 仓库地址（{MAX_REPOS} 个以内）</Label>
              <div className="space-y-2">
                {repos.map((repo, index) => (
                  <div key={index} className="flex gap-2">
                    <Input
                      value={repo}
                      placeholder="https://github.com/<owner>/<repo>"
                      inputMode="url"
                      autoComplete="off"
                      onChange={(e) => setRepo(index, e.target.value)}
                    />
                    {repos.length > 1 && (
                      <Button
                        type="button"
                        variant="outline"
                        size="icon"
                        aria-label="删除该仓库"
                        onClick={() => removeRepo(index)}
                      >
                        ×
                      </Button>
                    )}
                  </div>
                ))}
              </div>
              {repos.length < MAX_REPOS && (
                <Button type="button" variant="outline" size="sm" onClick={addRepo}>
                  + 添加仓库
                </Button>
              )}
            </div>

            {/* 隐私告知（SRS 3.6.2：上传前告知并取得同意） */}
            <div className="rounded-lg border bg-muted/40 p-4 text-sm leading-relaxed">
              <p className="font-medium">隐私告知</p>
              <ul className="mt-1.5 list-disc space-y-1 pl-5 text-muted-foreground">
                <li>
                  上传的简历仅用于生成本场面试的结构化画像与提问依据；
                  原件以随机文件名存放，保留 30 天后自动删除，可随时删除面试记录一并清除。
                </li>
                <li>
                  系统只检索你提交的公开仓库与公开网页信息，不查询私密数据；
                  请勿在简历中包含与求职无关的敏感信息。
                </li>
              </ul>
              <label className="mt-3 flex items-center gap-2 font-normal">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                  className="size-4 accent-primary"
                />
                我已阅读并同意上述隐私告知
              </label>
            </div>
          </CardContent>
          <CardFooter className="flex flex-col items-stretch gap-3">
            {error ? (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={!canSubmit}>
              {submitting ? "正在上传……" : "上传并开始解析"}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              <Link href="/" className="text-primary underline-offset-4 hover:underline">
                返回首页
              </Link>
            </p>
          </CardFooter>
        </Card>
      </form>
    </main>
  );
}

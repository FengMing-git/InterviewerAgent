"use client";

/**
 * 状态徽标：后台任务五态 + 面试八态的中文与配色（准备页 / 首页共用）。
 */

import { Badge } from "@/components/ui/badge";
import type { InterviewStatus, TaskStatus } from "@/types/api";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning" | "info" | "muted";

const TASK_STATUS: Record<TaskStatus, { label: string; variant: BadgeVariant }> = {
  pending: { label: "等待中", variant: "muted" },
  running: { label: "进行中", variant: "info" },
  done: { label: "已完成", variant: "success" },
  partial: { label: "部分完成", variant: "warning" },
  failed: { label: "失败", variant: "destructive" },
};

const INTERVIEW_STATUS: Record<InterviewStatus, { label: string; variant: BadgeVariant }> = {
  preparing: { label: "准备中", variant: "muted" },
  awaiting_confirm: { label: "待确认画像", variant: "warning" },
  initializing: { label: "初始化中", variant: "info" },
  ready: { label: "就绪", variant: "success" },
  queued: { label: "排队中", variant: "warning" },
  running: { label: "面试进行中", variant: "info" },
  finished: { label: "已结束", variant: "success" },
  aborted: { label: "已中止", variant: "destructive" },
};

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  const meta = TASK_STATUS[status] ?? { label: status, variant: "muted" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

export function InterviewStatusBadge({ status }: { status: InterviewStatus }) {
  const meta = INTERVIEW_STATUS[status] ?? { label: status, variant: "muted" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

export function interviewStatusLabel(status: InterviewStatus): string {
  return INTERVIEW_STATUS[status]?.label ?? status;
}

"use client";

/**
 * 标签输入器：技能 / 技术栈等字符串列表的编辑控件。
 * 回车或逗号提交，点击 × 删除；受控组件（value 为 string[]）。
 */

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

interface TagInputProps {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}

export function TagInput({
  value,
  onChange,
  placeholder,
  className,
  disabled,
}: TagInputProps) {
  const [draft, setDraft] = useState("");

  function commit() {
    const items = draft
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter((s) => s && !value.includes(s));
    if (items.length) onChange([...value, ...items]);
    setDraft("");
  }

  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex min-h-8 flex-wrap items-center gap-1.5 rounded-lg border border-input bg-transparent px-1.5 py-1 dark:bg-input/30">
        {value.map((tag) => (
          <Badge key={tag} variant="secondary" className="pr-1">
            {tag}
            {!disabled && (
              <button
                type="button"
                aria-label={`删除 ${tag}`}
                className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-destructive"
                onClick={() => onChange(value.filter((t) => t !== tag))}
              >
                ×
              </button>
            )}
          </Badge>
        ))}
        <Input
          value={draft}
          disabled={disabled}
          placeholder={value.length ? "" : (placeholder ?? "回车或逗号添加")}
          className="h-6 min-w-24 flex-1 border-0 px-1.5 focus-visible:border-transparent focus-visible:ring-0 dark:bg-transparent"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === "," || e.key === "，") {
              e.preventDefault();
              commit();
            } else if (e.key === "Backspace" && !draft && value.length) {
              onChange(value.slice(0, -1));
            }
          }}
          onBlur={commit}
        />
      </div>
    </div>
  );
}

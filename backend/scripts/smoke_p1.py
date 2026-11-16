"""P1 冒烟脚本（真实环境，消耗少量 token）：上传 → 解析 → 分析 → 确认画像。

用法：后端起来后 `cd backend && uv run python scripts/smoke_p1.py`
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"

RESUME = """\
张三（Zhang San）
某大学 计算机科学与技术 本科，2027 年 6 月毕业
邮箱 zhangsan@example.com

技能：Python, FastAPI, MySQL, Redis, Docker

项目经历：
1. Hello-World 示例系统（2026.03 - 2026.09）
   担任后端负责人。基于 Python 的演示项目，包含核心脚本与 README 文档，
   仓库地址 https://github.com/octocat/Hello-World
2. 校园二手交易平台（2025.09 - 2026.02）
   全栈开发，技术栈 Vue / Spring Boot / MySQL。

获奖：蓝桥杯省赛二等奖（2025）

实习：某互联网公司 后端开发实习生（2026.07 - 2026.09），参与订单模块开发。
"""


def main() -> int:
    with httpx.Client(timeout=30) as client:
        # 0) 健康检查
        health = client.get(f"{BASE}/health").json()
        print(f"[0] health: {health}")

        # 1) 注册 / 登录
        username = f"smoke{int(time.time()) % 100000}"
        res = client.post(f"{BASE}/auth/register", json={"username": username, "password": "password123"})
        res.raise_for_status()
        token = res.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"[1] 注册成功：{username}")

        # 2) 上传简历 + 仓库
        res = client.post(
            f"{BASE}/interviews",
            files={"resume": ("resume.txt", RESUME.encode("utf-8"), "text/plain")},
            data={"repo_urls": ["https://github.com/octocat/Hello-World"]},
            headers=headers,
        )
        res.raise_for_status()
        interview_id = res.json()["id"]
        print(f"[2] 创建面试：#{interview_id}（202）")

        # 3) 轮询任务直到 awaiting_confirm / failed（真实 LLM，耐心等）
        deadline = time.time() + 300
        last = ""
        while time.time() < deadline:
            detail = client.get(f"{BASE}/interviews/{interview_id}", headers=headers).json()
            marker = f"parse={detail['parse_status']} analysis={detail['analysis_status']} status={detail['status']}"
            if marker != last:
                print(f"[3] {marker}")
                last = marker
            if detail["status"] == "awaiting_confirm":
                break
            if detail["parse_status"] == "failed" or detail["analysis_status"] == "failed":
                print(f"[!] 任务失败：{detail['warnings']}")
                return 1
            time.sleep(3)

        if detail["status"] != "awaiting_confirm":
            print("[!] 超时未进入 awaiting_confirm")
            return 1

        profile = detail["profile"]
        print(f"[4] 画像：{profile['basic']['name']} / {profile['basic']['school']} / 技能 {profile['skills']}")
        for project in profile["projects"]:
            print(f"    项目 {project['id']}：{project['name']}（{project.get('repo_url') or '无仓库'}）")

        for report in detail["project_reports"] or []:
            repo = report["repo"]
            print(
                f"[5] 项目分析：{repo['url']} ★{repo['stars']} "
                f"语言 {repo.get('language_ratio')}"
            )
            print(f"    摘要：{(report.get('readme_summary') or '')[:80]}")
            print(f"    模块：{[m['name'] for m in report['key_modules']]}")
            for finding in report["findings"]:
                print(f"    {finding['id']}: {finding['text'][:60]} → {finding['source_url']}")

        # 6) 修改画像并确认
        profile["basic"]["name"] = "张三（已核对）"
        body = {"profile": profile, "config": {"style": "严谨", "difficulty": 2, "position": "后端"}}
        res = client.put(f"{BASE}/interviews/{interview_id}/profile", json=body, headers=headers)
        res.raise_for_status()
        confirmed = res.json()
        print(
            f"[6] 确认成功：status={confirmed['status']} "
            f"config={confirmed['config']} init={confirmed['init_status']}"
        )

        # 7) 列表
        listing = client.get(f"{BASE}/interviews", headers=headers).json()
        print(f"[7] 历史面试：{[(i['id'], i['status']) for i in listing]}")

        print("\n冒烟通过：上传 → 画像 + 项目分析 → 编辑并确认 ✔")
        return 0


if __name__ == "__main__":
    sys.exit(main())

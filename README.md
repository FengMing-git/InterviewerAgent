# InterviewerAgent

> 基于 LangChain 的 AI 面试官 Agent：上传简历，接受一场**有据可依**的模拟技术面试。
>
> 软件工程课程实践大作业 · 三人小组

正在开发中（当前进度：P1 解析 —— 上传简历与仓库地址后自动生成结构化画像与项目分析报告，可在准备页编辑并确认，实施计划见 `Plan.md`）

## 本地启动

1. 安装MySQL并在MySQL中创建intervieweragent数据库
2. 根据`backend/env.example`配置`backend/env`

3. 新建`frontend/.env.development`配置Next.js 项目的环境变量，把API 的基础地址设为本地后端服务的路径

```txt
# ── 必填 ───────────────────────────────────────────────────────────────────
# 接口地址：默认指向本地 FastAPI
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000/api/v1
```

4. 运行前端进行开发

```bash
cd frontend
npm install
npm run dev
```

5. 运行后端

```bash
cd backend
uv run python -m app.main
```

6. 前端发布构建（静态导出 out/ 自动拷到 backend/static/，由后端同域托管）

```bash
cd frontend && npm run build
```

7. 开发期浏览器访问 http://localhost:3000；发布期直接访问 http://127.0.0.1:8000

## 测试

```bash
uv run pytest            # 全量（含真实调 LLM 的连通性测试）
uv run pytest -k "not ping"   # 跳过在线 LLM 调用
```

数据库测试需要 MySQL 可连；连不上时相关用例自动 skip（不 fail）。

## 接口说明

接口前缀 `/api/v1`，除 `POST /auth/*` 与 `GET /health` 外均需 `Authorization: Bearer <JWT>`

## 许可

见 [LICENSE](LICENSE)。


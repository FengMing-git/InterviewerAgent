# InterviewerAgent

> 基于 LangChain 的 AI 面试官 Agent：上传简历，接受一场**有据可依**的模拟技术面试。
>
> 软件工程课程实践大作业 · 三人小组

正在开发中（当前进度：P0 骨架 —— 认证 / 健康检查 / 前后端打通已就绪，实施计划见 `Plan.md`）

## 本地启动

前置：配置 `backend/.env`（照 `backend/.env.example` 填），并保证 MySQL 8 可连（`DATABASE_URL`）。

```bash
# 后端（FastAPI，127.0.0.1:8000）
cd backend && uv run python -m app.main

# 前端开发模式（next dev，3000 端口，接口走 NEXT_PUBLIC_API_BASE）
cd frontend && npm install && npm run dev

# 前端发布构建（静态导出 out/ 自动拷到 backend/static/，由后端同域托管）
cd frontend && npm run build
```

- 开发期浏览器访问 http://localhost:3000；发布期直接访问 http://127.0.0.1:8000
- 接口前缀 `/api/v1`，除 `POST /auth/*` 与 `GET /health` 外均需 `Authorization: Bearer <JWT>`

## 测试

```bash
uv run pytest            # 全量（含真实调 LLM 的连通性测试）
uv run pytest -k "not ping"   # 跳过在线 LLM 调用
```

数据库测试需要 MySQL 可连；连不上时相关用例自动 skip（不 fail）。

## 许可

见 [LICENSE](LICENSE)。

## 使用说明

1. 安装MySQL并在MySQL中创建intervieweragent数据库
2. 配置backend\.env
```example
# ── 必填 ───────────────────────────────────────────────────────────────────
# API Key，形如 sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LLM_API_KEY=
# 模型名称
LLM_MODEL=
# base url
LLM_BASE_URL=
# Tavily API Key，形如 tvly-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TAVILY_API_KEY=
# 数据库端口，root后面密钥记得修改
DATABASE_URL=mysql+pymysql://root:ia123456@127.0.0.1:3306/intervieweragent?charset=utf8mb4

# JWT 签名密钥：openssl rand -hex 32
JWT_SECRET=
# 32 字节密钥的 base64（AES-256-GCM，加密用户自定义 API Key）
# 生成：uv run python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
APP_ENCRYPTION_KEY=

# ── 可选 ───────────────────────────────────────────────────────────────────
# GitHub token
GITHUB_TOKEN=
# 代理
HTTP_PROXY=
```
3. 配置frontend\.env
```example
# ── 必填 ───────────────────────────────────────────────────────────────────
# 接口地址：默认指向本地 FastAPI
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000/api/v1
```
4. 构建前端产物并运行
```bash
cd .\frontend\
npm install
npm run build
npm run dev
```
5. 运行后端
```bash
uv run python -m app.main
```
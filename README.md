# InterviewerAgent

> 基于 LangChain 的 AI 面试官 Agent：上传简历，接受一场**有据可依**的模拟技术面试。
>
> 软件工程课程实践大作业 · 三人小组

## 项目简介

求职者上传简历后，系统自动解析其中的公司经历、项目细节与博客 / GitHub 链接，并借助联网检索核实与扩展这些信息；随后 Agent 以真实面试官的口吻，针对简历中的 GitHub 项目深挖技术选型与实现细节、追问技术栈底层原理，并依据核实出的证据对简历中的夸大表述与逻辑漏洞发起挑战式提问；面试结束后输出多维度评估报告与改进建议，帮助求职者提前暴露问题、打磨表达。

与普通"聊天式模拟面试"的根本区别在于——**面试官提出的每一句质疑都有出处**：

1. 系统先做一轮背景调查：查 GitHub 仓库的真实提交作者分布、搜索公司信息、抓取博客正文；
2. 把候选人简历中的自述原话（`claims`，如"独立设计秒杀系统、QPS 5 万"）逐条与外部证据比对，产出**证据卡**（verified / contradicted / suspicious / unverifiable）与**风险标记**；
3. 挑战提问所依据的证据由**代码注入**，而非模型自行挑选；人设中另有"不得虚构仓库细节"的硬约束，报告引文也只允许引用真实 `message_id` —— 从机制上杜绝面试官幻觉，而非仅靠提示词祈祷。

### 四项课程技术点的落地

| 技术点 | 落地位置 |
| --- | --- |
| 提示工程 | 面试官人设 + 五阶段指令 + 两档难度 + 防幻觉硬约束（`agent/prompts.py`） |
| Function Calling | 联网核实 ReAct Agent 与 4 个工具（GitHub 仓库信息 / README / Tavily 搜索 / 网页抓取） |
| 网页信息抽取 | 简历文件解析（PDF/DOCX/TXT/MD）+ 博客正文抽取 |
| 记忆管理 | 长期档案注入（画像 + 证据卡 + 风险标记）+ 短期全量对话历史 + MySQL 持久化回放 |

## 核心流程

```
上传简历 → ① 简历解析（结构化画像 + 人工确认）
        → ② 联网核实（后台任务，产出证据卡与挑战议程）
        → ③ 模拟面试（五阶段：开场 → 项目深挖 → 原理追问 → 挑战提问 → 收尾）
        → ④ 评估报告（5 维度雷达图 + 引文锚定 + 改进计划）
```

面试阶段迁移由**纯 Python 状态机**控制（计数器 + 用户控制指令），LLM 只负责在当前阶段内生成提问——可测试、可回放。候选人的每次回答是一次独立的 `POST` 请求，响应体为 **NDJSON 事件流**（逐 token 推送），前端用 `fetch` + `getReader()` 逐行解析；消息先落库再生成，流中断后重开页面即可回放恢复。

## 技术栈

| 层 | 选型 |
| --- | --- |
| LLM | DeepSeek（OpenAI 兼容协议接入，模型名走环境变量 `LLM_MODEL`） |
| Agent 框架 | LangChain 1.x（`create_agent` ReAct 循环 / `with_structured_output` / `astream`） |
| 后端 | FastAPI + Uvicorn（async + `StreamingResponse`） |
| ORM / 数据库 | SQLAlchemy 2.0 + MySQL 8（InnoDB / utf8mb4） |
| 简历解析 | PyMuPDF、python-docx、纯文本直读 |
| 联网核实 | GitHub REST API、Tavily 搜索、httpx 网页抓取 |
| 前端 | Next.js + React + TypeScript + Tailwind CSS 4 + Recharts（静态导出，由 FastAPI 托管，单进程部署） |
| 测试 | pytest（后端）、Vitest（前端） |

## 目录结构

> 这是**目标结构**：✅ 已落地，⬜ 未开始（逐项状态见[开发进度](docs/开发进度.md)）。

```text
InterviewerAgent/
├── pyproject.toml / uv.lock      # Python 依赖（uv 管理）
├── docs/
│   ├── design/                   # 技术设计方案、项目目录结构
│   └── course/                   # 课程文档模板与要求
├── backend/
│   ├── .env.example              # 配置模板（模型 / 密钥 / 数据库 / 代理）
│   ├── app/
│   │   ├── main.py               # ⬜ FastAPI 入口、CORS、lifespan、静态托管
│   │   ├── config.py             # ✅ 配置唯一出口（pydantic-settings）
│   │   ├── api/v1/               # ⬜ 路由：resumes / sessions / interview / reports
│   │   ├── models/
│   │   │   ├── orm.py            # ✅ 4 张业务表
│   │   │   └── schemas.py        # ✅ 四大数据契约
│   │   ├── common/               # ✅ db / events（NDJSON 协议）
│   │   └── agent/                # ⬜ 简历解析、联网核实、面试引擎、评估报告
│   └── tests/                    # ✅ pytest 用例（不依赖 .env 与 MySQL）
└── frontend/                     # ⬜ 页面、聊天组件、报告图表（脚手架待搭建）
    └── types/                    # ✅ 契约镜像 index.ts
```

完整的目录说明见 [`docs/design/项目目录结构.md`](docs/design/项目目录结构.md)。

## 快速开始

### 环境要求

- Python 3.13+ 与 [uv](https://docs.astral.sh/uv/)
- Docker（用于启动 MySQL 8 演示库）
- Node.js 22+（前端）

### 1. 安装依赖

```bash
uv sync
```

### 2. 启动数据库

```bash
# 首次创建
docker run -d --name ia-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=ia123456 -e MYSQL_DATABASE=intervieweragent \
  -e MYSQL_ROOT_HOST=% mysql:8.0 \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci

# 已创建过
docker start ia-mysql
```

建表由应用启动时的 `Base.metadata.create_all` 自动完成，无需手工执行 SQL。

### 3. 配置环境变量

```bash
cp backend/.env.example backend/.env
```

**四项必填**：`LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`、`TAVILY_API_KEY` —— 缺任意一项，服务启动时会直接报错并点名缺哪个。模型名与网关地址必须与实际接入通道一致（团队当前走阿里云百炼 MaaS 网关，具体值问组内同学要，或照抄已配好的 `backend/.env`）。

`GITHUB_TOKEN` 可选，不填则 GitHub 接口限流 60 次/小时（一轮核实约消耗 6~10 次）；国内网络访问 GitHub / Tavily 不通时按需填 `HTTP_PROXY`（只作用于境外请求，不要套到 LLM 网关与本地 MySQL 上）。

> 留空即留空，**不要写占位符字符串**（空值等于没填）。超时、重试次数、核实轮数上限这类调参项**不在 `.env` 里**，直接改 `backend/app/config.py` 顶部的「调参常量」一节。

### 4. 启动后端

```bash
cd backend && uv run python -m app.main        # 生产/演示：单进程，一条命令
cd backend && uv run uvicorn app.main:app --reload   # 开发期热重载
```

启动后访问 <http://127.0.0.1:8000/health> 自检（只探数据库连通，不实际调用 LLM），接口文档见 <http://127.0.0.1:8000/docs>。

### 5. 前端

```bash
cd frontend && npm install && npm run dev      # 开发期（localhost:3000，后端已放行 CORS）
npm run build                                  # 发布期：静态导出 → 拷入 backend/static/ → 由 FastAPI 托管
```

前端脚手架尚未搭建，当前仅有契约镜像 `frontend/types/index.ts`（见[开发进度](docs/开发进度.md)）。

### 6. 运行测试

```bash
uv run pytest        # 仓库根目录执行即可（pyproject 已配好 backend/ 导入路径）
```

不需要起 MySQL，也不需要配 `.env`：`backend/tests/conftest.py` 会在导入任何 `app.*`
之前塞好占位配置（`config.py` 的四个必填项在 import 时就会校验，没配会直接挂在收集阶段）。
需要真实数据库的用例请在自己的 fixture 里显式覆盖 `DATABASE_URL`。

## 开发进度与 TODO

模块状态总览见 [`docs/开发进度.md`](docs/开发进度.md)，待办任务清单见 [`docs/TODO.md`](docs/TODO.md)。

## 文档

| 文档 | 说明 |
| --- | --- |
| [`docs/design/技术设计方案.md`](docs/design/技术设计方案.md) | 主技术文档：架构、选型论证、核心链路、数据库、API、前端、风险对策、里程碑 |
| [`docs/design/项目目录结构.md`](docs/design/项目目录结构.md) | 完整目录树与各文件职责 |
| [`docs/开发进度.md`](docs/开发进度.md) | 模块状态总览（✅ / 🟡 / ⬜） |
| [`docs/TODO.md`](docs/TODO.md) | 按设计方案 §9 三个工作项组织的任务清单 |
| `docs/course/` | 课程文档模板与要求 |

## 许可

见 [LICENSE](LICENSE)。
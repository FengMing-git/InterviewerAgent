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

完整的目录说明见 [`docs/design/项目目录结构.md`](docs/design/项目目录结构.md)。

## 许可

见 [LICENSE](LICENSE)。

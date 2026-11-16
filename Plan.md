# InterviewerAgent 编码方案（Plan）

> 定位：写代码时照着做的实施方案。课程文档（概要设计 / 详细设计 / 测试计划）由本文扩写。
> 依据（冲突时以前者为准）：`docs/需求规格说明书.docx`（下称 SRS）＞ `docs/可行性研究报告.docx`（下称可研）＞ `docs/模块划分报告.md`、`docs/需求分析.md`。
> 约定：SRS 中的时间指标（首字 3 s、核验 90 s 等）先作为优化目标，不作为第一版的硬约束；超时与重试仍按 `config.py` 实现。

---

## 1. 技术栈

| 层 | 选型 | 备注 |
| --- | --- | --- |
| 语言 / 环境 | Python 3.13 + uv | 已有 `pyproject.toml` |
| Web | FastAPI + Uvicorn | REST + SSE（`StreamingResponse`，`text/event-stream`，手写事件格式） |
| Agent / LLM | LangChain 1.x + langchain-openai（OpenAI 兼容协议）+ langchain-tavily | 核验用 `create_agent`（ReAct）；结构化输出用 `with_structured_output` |
| 数据库 | MySQL 8（InnoDB，utf8mb4）+ SQLAlchemy 2.0（同步）+ PyMySQL | `create_all` 建表，不用迁移工具 |
| 简历解析 | PyMuPDF（PDF）、python-docx（DOCX）、直接读取（TXT/MD） | |
| 外部 HTTP | httpx | GitHub REST、网页抓取 |
| 安全 | bcrypt（密码）、PyJWT（登录）、cryptography AESGCM（自定义 API Key） | 需新增依赖 `bcrypt`、`pyjwt` |
| 后台任务 | FastAPI `BackgroundTasks`（进程内） | 单进程部署，不引入 Celery/Redis |
| 前端 | Next.js（App Router，`output: "export"` 静态导出）+ TypeScript + Tailwind CSS + shadcn/ui | 构建产物 `out/` 拷到 `backend/static/` 由 FastAPI 托管 |
| 前端库 | eventsource-parser（POST 流式读取 SSE）、react-markdown（报告）、recharts（雷达图） | |
| 测试 | pytest + pytest-asyncio；LLM 用 `GenericFakeChatModel`，httpx 用 mock | 前端第一版只配 ESLint，不写测试 |

全部同步实现：接口写普通 `def`，流式接口用同步生成器，由 FastAPI 放进线程池执行。

---

## 2. 总体架构与模块对应

```
浏览器（Next.js 静态页面）
   │ REST(JSON) / multipart / SSE
FastAPI（api/）── 鉴权（JWT）、参数校验、错误码
   │
业务服务（services/）── 8 个 SRS 功能模块
   │
Agent 层（agent/）── LLM 适配、提示词、工具
   │
MySQL（3 张表）  uploads/（简历原件）  外部：阿里云百炼 / Tavily / GitHub
```

| SRS 功能模块 | 模块划分报告 | 代码位置 |
| --- | --- | --- |
| 1 用户管理 | M2 / M12 | `api/auth.py`、`api/users.py`、`core/security.py` |
| 2 简历解析 | M4 | `services/resume_parser.py` |
| 3 项目分析 | M5（拆出） | `services/project_analysis.py` |
| 4 联网背景核验 | M5 | `services/research.py`（面试前）、`services/answer_verify.py`（面试中） |
| 5 面试官初始化 | M6 | `services/interviewer_init.py` |
| 6 面试流程控制 | M7 / M11 | `services/state_machine.py`、`services/interview_engine.py` |
| 7 幻觉修正 | M8 | `services/hallucination.py` |
| 8 面试评估报告 | M9 | `services/report.py` |
| 大模型适配 | M13 | `agent/llm.py` |
| 外部检索工具 | M14 | `agent/tools/` |
| 数据持久化 | M12 | `models/orm.py`、`core/db.py` |

---

## 3. 目录结构

```
backend/
├── .env / .env.example
├── app/
│   ├── main.py                # FastAPI 实例、lifespan（建表、僵尸任务重置、过期简历清理）、路由挂载、静态托管 + SPA fallback
│   ├── config.py              # Settings（.env）+ 调参常量
│   ├── core/
│   │   ├── db.py              # engine / SessionLocal / get_db
│   │   ├── security.py        # bcrypt、JWT 签发与校验、AES-256-GCM 加解密
│   │   ├── deps.py            # get_current_user、get_owned_interview（资源归属校验）
│   │   ├── errors.py          # AppError(code, message, detail) + 全局异常处理
│   │   ├── sse.py             # sse_event(event, data) 格式化
│   │   └── logger.py          # 日志（脱敏：不打印密码、key、简历全文）
│   ├── models/
│   │   ├── orm.py             # User / Interview / Message
│   │   └── schemas.py         # D1–D5、报告、API 请求/响应模型
│   ├── api/
│   │   ├── auth.py            # 注册 / 登录
│   │   ├── users.py           # 个人资料、自定义模型配置
│   │   ├── interviews.py      # 创建、查询、确认画像、重试任务、跳过、结束、删除、消息列表
│   │   ├── stream.py          # SSE：开始面试、提交回答
│   │   └── reports.py         # 报告 JSON、Markdown 导出
│   ├── services/              # 见 §2 表
│   │   ├── quota.py           # 每日免费额度、活动会话上限（排队）
│   │   └── cleanup.py         # 30 天简历原件清理
│   └── agent/
│       ├── llm.py             # get_llm(user, purpose)：用户自定义配置优先，否则项目密钥
│       ├── prompts.py         # 人设、阶段指令、难度档位、防幻觉约束、公平性约束
│       └── tools/
│           ├── github.py      # get_user / get_repo / list_tree / read_file / get_contributions
│           ├── search.py      # web_search（Tavily）
│           └── web.py         # web_fetch（正文抽取、拒绝内网地址）
├── uploads/                   # 简历原件（uuid 文件名，不入库）
├── static/                    # Next.js 导出产物（不入库）
└── tests/
frontend/                      # Next.js 项目
```

---

## 4. 数据库（SRS 3.4：3 张表）

字符集 utf8mb4，时间字段统一 `DATETIME(3)`，UTC+8。

### 4.1 `users`

| 字段 | 说明 |
| --- | --- |
| id PK | |
| username UNIQUE | 4～32 位，字母、数字、下划线 |
| password_hash | bcrypt |
| display_name, target_position | 个人资料 |
| llm_base_url, llm_model | 自定义模型配置，可空 |
| llm_key_cipher, llm_key_nonce | AES-256-GCM 密文（含认证标签）与随机数，可空；接口不回传明文 |
| quota_date, quota_used | 当日已用免费场数（跨日重置） |
| created_at, updated_at | |

### 4.2 `interviews`（一场面试一条，结构化数据都以 JSON 列存放）

| 字段 | 说明 |
| --- | --- |
| id PK, user_id FK | |
| status | `preparing` → `awaiting_confirm` → `initializing` → `ready` → `queued` / `running` → `finished` / `aborted` |
| stage | 1～5（开场、项目深挖、原理追问、挑战提问、收尾） |
| parse_status / analysis_status / research_status / init_status / report_status | 每个后台任务：`pending / running / done / partial / failed` |
| resume_path | `uploads/{uuid}{ext}`，30 天后删除并置空 |
| repo_urls JSON | 1～3 个公开仓库地址 |
| profile JSON | D1 简历结构化画像 |
| project_reports JSON | D2 项目分析报告列表 |
| verification_items JSON | D3 核验条目列表 |
| persona JSON | D4 面试官人设 |
| directions JSON | D5 提问方向（附运行时字段，见 §5） |
| config JSON | `{style, difficulty(1-3), position}` |
| report JSON, total_score | 面试评估报告、总分（1 位小数） |
| token_usage JSON | `{input, output}` 累计 |
| generating BOOL | 生成中标记，用于并发互斥 |
| uses_free_quota BOOL | 本场是否占用免费额度 |
| warnings JSON | 解析、核验过程中的提示信息 |
| created_at, started_at, ended_at, updated_at | |

### 4.3 `messages`

| 字段 | 说明 |
| --- | --- |
| id PK, interview_id FK | |
| seq | 场内序号，从 1 开始 |
| client_msg_id | 候选人消息的幂等键；`UNIQUE(interview_id, client_msg_id)` |
| role | `interviewer` / `candidate` |
| stage, direction_id | 所属阶段与提问方向 |
| content | 最终文本（面试官消息 ≤ 300 字，纯文本） |
| original_content, corrected | 幻觉修正前的原文、修正标记（`none / fixed / fallback`） |
| is_followup | 是否追问 |
| evidence_ids JSON | 引用的证据 ID |
| verify_result JSON | 面试中对该回答的核实结论 |
| answer_duration_s, response_ms | 作答用时、响应耗时 |
| created_at | |

删除面试时级联删除消息和简历原件。

---

## 5. 数据契约（`models/schemas.py`）

字段沿用 SRS 表 3-3-2，下面只列补充约定。

**证据 ID 命名空间**（所有 `evidence_ids` 都从这里取）：

| 前缀 | 来源 |
| --- | --- |
| `C-001` | D3 核验条目 |
| `F-001` | D2 项目分析报告中的 `findings[]`（每条带 `id`、`text`、`source_url`） |
| `V-001` | 面试中回答核实产生的结论（存在 `messages.verify_result`） |

- **D1 画像**：`basic{name, school, major, degree, grad_date}`、`skills[]`、`projects[{id, name, role, period, tech_stack[], description, repo_url}]`、`awards[]`、`internships[]`；原文只写到年份就保留年份，GPA 和排名保留原文精度。
- **D2 项目分析报告**：`repo{url, stars, forks, language_ratio, last_commit_at}`、`readme_summary`、`tech_stack[]`、`key_modules[]`、`findings[]`、`related_project_id`。
- **D3 核验条目**：`id, claim, status(verified|contradicted|suspicious|unverifiable), evidence[{url, title, snippet}], note, risk_level(low|medium|high)`；访问不到的来源归入 `unverifiable`，原因写在 `note`。
- **D4 人设**：`name, style, difficulty(1-3), position, opening`。
- **D5 提问方向**：`{id, stage(1-5), topic, evidence_ids[≥1], priority(1-3)}`，运行时附加 `state(pending|active|done)` 和 `followups_used`。
- **报告**：`dimensions[5]{key, score(0-100 整数), comment, message_ids[], evidence_ids[]}`、`stage_performance[]`、`issues[]`、`improvement_plan[{problem, action, priority}]`、`summary`；`total_score` 由代码按权重加权计算（30/25/20/15/10，四舍五入保留 1 位小数），不由 LLM 生成。

所有 LLM 结构化输出先经 Pydantic 校验；校验不通过就带着错误信息重新生成一次，仍失败则任务置 `failed`。

---

## 6. 核心流程

### 6.1 准备阶段（用户 3 步：上传 → 确认 → 开始）

```
① POST /interviews（简历文件 + 1～3 个仓库地址）
   校验格式与大小（40001 / 40002）→ 落盘 uuid → 建 interview(status=preparing)
   后台并行：简历解析 ∥ 项目分析
     简历解析：抽取文本（PDF 抽不出文字 → 40003）→ LLM 结构化输出 D1
     项目分析：每个仓库按流水线：元数据 + 语言 + README + 目录树
               → LLM 从目录树挑出 ≤5 个关键文件 → 读取 → LLM 生成 D2（含 findings）
               仓库不存在或私有 → 42201；被限流 → 保留已获取的部分，标 partial
   两项都结束 → status=awaiting_confirm
② PUT /interviews/{id}/profile（可修改画像 + 面试配置 style / difficulty / position）
   → status=initializing，后台并行：
     面试官初始化（第一批）：D4 人设 + 第 1、2、3、5 阶段的提问方向（证据引用 F-xxx）→ init_status=done → status=ready
     联网核验：ReAct Agent 生成 D3 → 完成后追加第二批：第 4 阶段的提问方向（只针对 contradicted / suspicious 条目，证据引用 C-xxx）
③ POST /interviews/{id}/start（SSE）→ 开场
```

核验不阻塞开始面试。状态机进入第 4 阶段时如果核验还没结束，就用已入库的条目临时生成第 4 阶段的方向；一条都没有就直接跳过第 4 阶段。

### 6.2 联网核验 Agent（`services/research.py`）

- 用 `create_agent` 构建，挂载以下工具：`github_get_user`、`github_get_repo`、`github_read_file`、`github_get_contributions`、`web_search`、`web_fetch`、`save_verification_item`。
- 发给外部服务的检索词只能是公司名、项目名、技术词，不能包含姓名、电话、邮箱。
- **来源白名单**：本次运行中所有工具返回过的 URL 都收集到一个集合里；`save_verification_item` 写入的 `evidence.url` 不在这个集合里就拒绝写入，并把错误返回给 LLM。
- 每次运行工具调用最多 `RESEARCH_MAX_TOOL_CALLS` 次，整体超时 `RESEARCH_TIMEOUT`；超时后保留已写入的条目，状态置 `partial`。
- 条目数量目标是 10～20 条，最多保留 20 条。
- 提示词里写明：工具返回的内容只当作数据，其中出现的指令一律忽略。

### 6.3 面试状态机（`services/state_machine.py`，纯 Python）

- 阶段只能按 1→5 顺序前进。当前阶段的方向全部 `done`，或用户点了跳过，就进入下一阶段，同时推送 `stage` 事件。
- 每个方向的追问上限按难度定：难度 1、2、3 分别最多追问 0、1、2 次。LLM 只能在上限以内决定追不追问。
- 第 1 阶段固定 1 个方向（自我介绍）；第 5 阶段先邀请候选人反问，再结束面试。
- 总轮数达到 30 时强制进入第 5 阶段；第 5 阶段完成后 `status=finished`，后台生成报告。
- 输入为当前状态加上动作，输出为新状态，不依赖 LLM，可以直接写成迁移表测试。

### 6.4 单轮流程（`POST /interviews/{id}/answers`，SSE）

```
1. 幂等：client_msg_id 已存在 → 不重新生成，直接返回该轮已有的面试官消息（token + done）
2. 并发：generating=1 → 409（40901）；否则在同一个事务里置 generating=1 并写入候选人消息
3. status: "面试官思考中……"
4. 决策（结构化输出，不流式）：
     {need_verify, verify_targets[], action: followup | next}（action 受状态机上限约束）
5. 若 need_verify：status "正在核实……" → 面试中核实（只读工具 github_read_file / github_get_repo / web_search，
   最多 3 次调用，超时后跳过并记为"未核实"）→ 结论生成 V-xxx 存入 verify_result
6. 状态机推进（如果切换阶段，推送 stage 事件）→ 确定本轮的 direction 和可用证据集合
7. 生成候选问题（结构化输出）：{text, evidence_ids}，提示词只放可用证据
8. 规则校验（hallucination.check）：
     - evidence_ids 非空，且全部属于可用证据集合
     - text ≤ 300 字，不含 Markdown
     - 文中出现的仓库细节标识（文件路径、类名或函数名、带点的包名）必须出现在所引证据的文本里
     - 不涉及性别、地域、婚育
   不通过 → status "正在调整问题……" → 修正：把违规项反馈给 LLM，允许调用 github_read_file 补充证据，然后再校验一次
   仍不通过 → 备选问题：按 direction.topic + 证据模板生成，corrected=fallback
9. 定稿写入数据库（content、original_content、corrected、evidence_ids、response_ms）
10. 把定稿切成小段，用 token 事件推送 → done{message_id}
11. finally：generating=0
```

`start` 接口走的是同一套流程，只是没有候选人消息，并以人设开场白开头。开始前先做两项检查：
- **免费额度**：用户没有自定义模型配置时，检查当日已用场数是否超过 4，超过返回 42902。
- **排队**：活动会话（status=running）已有 10 个时，本场置为 `queued`，返回排队位置，前端轮询后重新调用 start。

### 6.5 评估报告（`services/report.py`）

- 输入：全部消息、画像、核验条目、作答用时。LLM 结构化输出报告，引文只填 `message_id` 和 `evidence_id`。
- 代码负责三件事：
  - 校验引文 ID 是否属于本场，不属于的丢弃并记 warning；
  - 按 ID 取回原文，渲染成引文；
  - 计算总分。
- 导出：`GET /report.md` 由后端生成 Markdown；PDF 用前端 `window.print()` 加打印样式。
- 页面和报告里都要标明：“内容由 AI 生成，仅供训练参考”。

### 6.6 LLM 适配（`agent/llm.py`）

- `get_llm(user, purpose, streaming=False)`：用户配置了自定义模型就用它（key 现场解密），否则用项目密钥。
- 统一设置 `timeout=LLM_TIMEOUT`、`max_retries=LLM_MAX_RETRIES`；不同用途使用不同的 temperature（解析和核验用 0，提问用 0.7，报告用 0.2）。
- 通过回调累计 token 用量，写入 `interviews.token_usage`。

### 6.7 启动与维护（`main.py` lifespan）

- 执行 `create_all` 建表。
- 处于 running 状态且 `updated_at` 超过 5 分钟的后台任务置为 `failed`；`generating=1` 一律复位为 0。
- 删除超过 30 天的简历原件，并清空对应的 `resume_path`。
- 任务失败后，前端可以调用 `POST /interviews/{id}/retry/{task}` 重新执行。

---

## 7. API（前缀 `/api/v1`，除注册和登录外都需要 `Authorization: Bearer <JWT>`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/auth/register`、`/auth/login` | 登录返回 JWT |
| GET / PUT | `/users/me` | 个人资料 |
| PUT / DELETE | `/users/me/llm-config` | 自定义模型配置；GET `/users/me` 只返回 `has_custom_llm` 和脱敏后的 key |
| POST | `/interviews` | multipart：简历文件 + repo_urls → 202 |
| GET | `/interviews` | 本人的历史面试列表 |
| GET | `/interviews/{id}` | 各任务状态、D1–D5、当前阶段（向导页轮询这个接口） |
| PUT | `/interviews/{id}/profile` | 确认画像和面试配置，触发初始化与核验 |
| POST | `/interviews/{id}/retry/{task}` | task ∈ parse / analysis / research / init / report |
| POST | `/interviews/{id}/start` | SSE：开场与第一问（会检查额度和排队） |
| POST | `/interviews/{id}/answers` | SSE：`{content(1-2000 字), client_msg_id, answer_duration_s}` |
| POST | `/interviews/{id}/skip`、`/finish` | 生成中时返回 409 |
| GET | `/interviews/{id}/messages?after_seq=` | 刷新恢复和回放 |
| GET | `/interviews/{id}/report`、`/report.md` | 报告 JSON、Markdown 导出 |
| DELETE | `/interviews/{id}` | 级联删除 |
| GET | `/health` | 检查配置是否齐全、数据库是否连通，不调用 LLM |

**SSE 事件**（SRS O2）：`stage {stage, name}`、`status {text}`、`token {text}`、`done {message_id, interview_status}`、`error {code, message}`。

**错误格式** `{code, message, detail}`：SRS 规定的有 40001、40002、40003、42201、42901、50401。另外补充以下几个：

| 错误码 | 含义 |
| --- | --- |
| 40101 | 未登录或 token 过期 |
| 40301 | 访问他人资源 |
| 40401 | 资源不存在 |
| 40901 | 正在生成中 |
| 42902 | 免费面试额度已用完 |
| 42202 | 当前状态不允许该操作 |

---

## 8. 前端（`frontend/`，Next.js 静态导出）

- 静态导出不支持动态路由，所以参数一律用查询参数，组件内用 `useSearchParams` 读取。
- 开发期：`next dev` 跑在 3000 端口，接口地址取 `NEXT_PUBLIC_API_BASE`，后端开启 CORS。
- 发布期：`next build` 生成 `out/`，拷到 `backend/static/`，由 FastAPI 托管，未匹配的路径回退到 `index.html`。

| 页面 | 路由 | 内容 |
| --- | --- | --- |
| 登录 / 注册 | `/login`、`/register` | |
| 首页 | `/` | 历史面试列表（状态、得分、时间），新建面试入口 |
| 新建 | `/new` | 第 1 步：上传简历、填仓库地址，附隐私告知，需勾选同意 |
| 准备 | `/prepare?id=` | 轮询任务状态 → 第 2 步：画像编辑和面试配置 → 显示核验条目（状态徽标、来源链接）→ 第 3 步：开始面试 |
| 面试 | `/interview?id=` | 阶段步骤条、流式消息、status 提示、证据角标（悬停显示条目内容）、跳过 / 结束按钮；刷新后通过 messages 接口恢复 |
| 报告 | `/report?id=` | 雷达图、维度卡片和引文、各阶段表现、改进计划、Markdown 下载、打印成 PDF、AI 生成标识 |
| 回放 | `/replay?id=` | 按时间线展示消息和证据 |
| 设置 | `/settings` | 个人资料、自定义模型配置 |

前端工具代码：
- `lib/api.ts`：封装 fetch，自动带 token，遇到 401 跳转登录页。
- `lib/sse.ts`：用 POST fetch 读流，交给 `eventsource-parser` 解析后分发事件。
- `types/`：手工保持与后端 schemas 一致。

---

## 9. 配置（`config.py`）

- **.env 必填**：在现有项目基础上新增 `JWT_SECRET`、`APP_ENCRYPTION_KEY`（32 字节密钥的 base64，用于 AES-GCM）。`.env.example` 同步更新。
- **新增调参常量**：
  - `SCORE_WEIGHTS`
  - `FREE_INTERVIEWS_PER_DAY = 4`
  - `MAX_ACTIVE_SESSIONS = 10`
  - `RESUME_RETENTION_DAYS = 30`
  - `MAX_FOLLOWUPS = {1: 0, 2: 1, 3: 2}`
  - `MAX_TURNS = 30`
  - `ANSWER_VERIFY_MAX_TOOL_CALLS = 3`
  - `QUESTION_MAX_CHARS = 300`
  - `ANSWER_MAX_CHARS = 2000`
  - `MAX_REPOS = 3`
  - `JWT_EXPIRE_HOURS`
  - `ZOMBIE_TASK_MINUTES = 5`

---

## 10. 测试（`backend/tests/`）

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_state_machine.py` | 阶段迁移表：方向完成、跳过、追问上限、30 轮强制收尾、第 4 阶段无方向时跳过 |
| `test_hallucination.py` | 规则校验各项（证据 ID、字数、Markdown、仓库标识、公平性），修正失败时回退到备选问题 |
| `test_research_whitelist.py` | 不在来源白名单里的证据 URL 被拒绝写入 |
| `test_idempotency.py` | 重复的 client_msg_id 不会重复生成；生成中再请求返回 409；异常后 generating 被复位 |
| `test_report.py` | 引文 ID 校验与回查、总分加权计算和四舍五入 |
| `test_resume_parser.py` | 四种格式能抽取文本；PDF 无文字层返回 40003；格式不支持和超大文件返回对应错误码 |
| `test_tools.py` | GitHub、网页工具的正常结果与 404、403、超时（httpx mock），web_fetch 拒绝内网地址 |
| `test_security.py` | bcrypt、JWT 过期、AES-GCM 加解密往返、访问他人资源返回 403 |
| `test_quota.py` | 每日额度跨日重置、超过 10 个活动会话时排队 |
| `test_llm_connectivity.py` | 已有，真实调用 LLM 的在线测试 |

涉及 LLM 的流程都用 `GenericFakeChatModel` 预设输出；测试数据库使用独立的库名。

---

## 11. 里程碑

| 阶段 | 内容 | 完成标志 |
| --- | --- | --- |
| **P0 骨架** | main.py、db、3 张表、错误码、注册登录 JWT、`/health`；Next.js 初始化 + 登录页 + 静态托管打通 | 浏览器能注册登录；`uv run pytest` 通过 |
| **P1 解析** | 简历解析、GitHub 工具、项目分析；前端新建页、准备页第 1、2 步 | 上传后能看到画像和项目分析，能编辑并确认 |
| **P2 核验 + 初始化** | 搜索和网页工具、核验 Agent（白名单、超时后 partial）、面试官初始化（两批提问方向）；准备页展示核验条目 | 确认画像后生成核验条目和提问方向 |
| **P3 面试** | 状态机、单轮流程、幻觉校验与修正、面试中核实、SSE、幂等与 409、刷新恢复；前端面试页 | 能完整跑完 5 个阶段，刷新页面能恢复 |
| **P4 报告** | 报告生成、引文回查、Markdown 导出；前端报告页、雷达图、回放页 | 结束面试后能看到并导出报告 |
| **P5 收尾** | 自定义模型配置、免费额度、排队、30 天清理、隐私告知与 AI 标识、补全测试、README 部署说明（含 HTTPS） | 第 10 节的测试全部通过 |

可以并行的部分：
- 前端在第 5、7 节的契约定下来后即可开工，用假数据开发。
- P3 可以直接往数据库写假的 D3、D5 后开发，不必等 P2。
- 工具层（`agent/tools/`）可以独立开发并测试。

---

## 12. 暂不实现（后续）

TTS 播报、ASR 语音作答、面试错题集、简历优化建议、按岗位调整考察侧重（`position` 字段先保留，只放进提示词）、多面试官、首字 3 s 等时间指标的优化。

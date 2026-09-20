# TODO

> 按[技术设计方案](design/技术设计方案.md) §9 的三个工作项组织，括号内为对应模块。模块状态总览见[开发进度.md](开发进度.md)。

**A 后端平台（M1 + M2）**

- [ ] 新建 `backend/app/main.py`：FastAPI 骨架（CORS、`/health`、启动建表）、挂载 `api/v1` 四组路由、发布期静态托管 + SPA fallback、lifespan 中重置僵尸核实任务（`research_status=running` 且 `updated_at` 早于 5 分钟）
- [ ] `common/storage.py`：`uploads/` 文件管理（uuid 命名、扩展名白名单、10MB 上限）
- [ ] `common/logger.py`：日志配置
- [ ] `api/v1/resumes.py`：上传 / 详情轮询 / 画像确认 / 重新核实 / 级联删除
- [ ] `api/v1/sessions.py`：创建 / 详情 / 消息回放 / 跳过 / 结束 / 删除
- [ ] `api/v1/interview.py`：NDJSON 流式接口（`client_msg_id` 幂等 + `generating` 并发互斥 + 单事务提交）
- [ ] `api/v1/reports.py`：报告查询
- [ ] `agent/tools.py`：4 个 `@tool`（GitHub 仓库信息 / README / Tavily 搜索 / 网页抓取）
- [ ] `agent/research.py`：`create_agent` 联网核实编排，含 12 轮工具调用上限与 120s 硬超时降级

**B Agent 智能（M3）**

- [ ] `agent/llm.py`：`ChatOpenAI` 工厂（按角色配温度与超时）
- [ ] `agent/prompts.py`：人设 + 5 阶段指令 + 2 难度档位 + 防幻觉硬约束
- [ ] `agent/interview.py`：五阶段状态机 + 单轮流式处理（NDJSON 生成器）
- [ ] `agent/report.py`：评估报告生成与引文硬校验

**C 前端与质量（M4 + 测试）**

- [ ] 前端脚手架：`package.json`、`next.config.ts`（`output: "export"`）、Tailwind 配置
- [ ] `lib/api.ts`：全部 fetch 封装与流式读取器；`components/`：ChatMessage / ChatInput / EvidenceBadge / PhaseStepper / RadarChart
- [ ] 4 个页面：首页、面试准备向导（三步）、面试聊天页、报告页
- [ ] `backend/tests/`：`test_phase_machine.py`（状态迁移表驱动）、`test_tools.py`、`test_api.py`、`test_idempotency.py`
- [ ] 前端 Vitest 用例

**课程文档**：可行性研究报告 → 需求规格说明书（DFD / 用例图）→ 概要设计说明书 → 详细设计说明书 → 测试计划（各章与技术设计方案的转写映射见设计方案 §10）。

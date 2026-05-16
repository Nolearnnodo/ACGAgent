# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目定位

ACGAgent 是数字人文领域的图数据库维护系统，核心目标是用 LLM Agent 把唐代墓志铭与正史列传等古汉语文本端到端地转换为 Neo4j 知识图谱，并支持后续维护与查询。当前论文/系统包含三大功能：

- **功能 A：文本知识抽取**（当前开发重点）——从原始古文一步到位生成符合 Neo4j schema 的结构化数据
- **功能 B：图谱维护与史实勘误**——同名人物消歧、CBDB 交叉验证、可信度优先级（墓志铭 > 正史 > CBDB > LLM 内部知识）
- **功能 C：信息查询**——NL→Cypher、意图路由、思维链问答

`bg_knowledge/` 目录（已加入 `.gitignore`）放置全部背景资料 PDF：`Agent系统结构图设计.pdf`、`整体过程.pdf`、`数据库存储格式（…）.pdf`、`年号数据库.pdf`、`历史事件数据库.pdf`、`人物分级指南.pdf`、`论文B内容.pdf`、`论文整体规划的AI设计.pdf`、`任务清单与问题讨论.pdf`。这些是设计权威来源，遇到 schema/流程歧义时优先以 PDF 内容为准。

## 常用命令

所有后端命令默认在 `backend/` 目录执行；前端命令默认在 `frontend/` 目录执行。

### 后端

```bash
pip install -r requirements.txt
pip install "bcrypt==4.0.1" --force-reinstall   # 必装，新版 bcrypt 与 passlib 不兼容
alembic upgrade head                            # 建表/迁移，不再依赖 create_all
uvicorn app.main:app --reload                   # 启动 API（http://localhost:8000/docs）

alembic revision --autogenerate -m "..."        # 修改 ORM 后生成迁移草稿（须人工核对）
alembic current / alembic history               # 查看版本

pytest backend/tests                            # 全量测试
pytest backend/tests/test_planner_executor.py::test_executor_runs_passage_ingestion_workflow   # 跑单测
```

### 前端

```bash
npm install
npm run dev      # 开发服 http://localhost:5173
npm run build    # vue-tsc --noEmit 类型检查 + vite 构建
```

### 环境变量

复制 `.env.example` 为 `.env` 后，至少需要正确填写 `LLM_API_KEY`（`LLM_PROVIDER=deepseek` 是推荐值）和 `NEO4J_*`。任何模块都必须经 `app.core.config.get_settings()` 读取，禁止直接 `os.environ`。

## 顶层架构

### 受控单 Agent（必读）

系统刻意采用**受控单 Agent**，**不允许 LLM 自由生成多步执行计划**。Planner 只能从既有 Skill / Workflow 中选一个目标，Workflow 内部也只能调用已注册的 Atomic / Workflow Skill。这条约束贯穿 `backend/app/agents/`、`backend/app/skills/` 与 `backend/app/services/conversation_service.py`，新功能也必须遵守。

执行链路：

```
ChatRouter → ConversationService → Planner.plan() → PlannerDecision
                                                  ↓
                              Executor.execute() → SkillRegistry.get()
                                                  ↓
                                ExecutionContext ⇄ Skill.run()
                                                  ↓
                          step1_result → Workflow 后续步 / DB 持久化
```

`ExecutionContext`（`backend/app/agents/context.py`）作为唯一上下文：
- `metadata` 在路由侧注入（如 `passage_ingestion` 把 passage dict 放进来）
- `step_results` 由 Executor / Workflow 写入（约定 key：`step1_result`、`step2_result`…）
- `resolve_value` 支持 `$step1_result.xxx` 形式的简单引用，未来可扩展成模板

### Skill 注册规范

- 所有 Skill 必须继承 `app.skills.base.BaseSkill`，必须设置 `code` 和 `allowed_roles`，并实现 `run(context, arguments) -> dict`
- **代码层注册**：`backend/app/skills/registry.py` 的 `_instances` 字典加入实例
- **元数据注册**：同文件 `register_builtin_metadata()` 的 `definitions` 列表加入条目（`code` 与代码层的实例 key 必须一致）；启动时会写入 SQLite `skill_definitions` 表
- **权限**：`Executor` 会先读 SQLite 中的 `allowed_roles_json`，缺失时回退到代码内的 `allowed_roles`，二者必须保持一致
- **写类 Skill**：写图、写文档、写古籍入库等必须 `allowed_roles=["admin"]`，且 Executor 会再次校验角色
- **目录约定**：纯执行单元放 `app/skills/atomic/`；编排多步骤的固定流程放 `app/skills/workflow/`，Workflow 不应包含未注册的硬编码逻辑

### Planner / LLM Provider 边界

- `Planner` 只对单条用户消息做意图分类；当前真正决策由 `app.llm.providers.factory.get_llm_provider()` 选出的 Provider 负责
- DeepSeek Provider 在解析失败、网络异常、配置缺失时会自动回退到 `MockLLMProvider`，因此本地不带 API key 时系统也能跑通
- `MockLLMProvider` 的关键词路由可以作为离线规则：含「写/新增/删除/修改图/写入图」走 `graph_write_atomic`（admin 否则 reject）；含「查询/查找/图谱/关系/节点」走 `graph_query_atomic`；其余走 `conversation_reply_workflow`
- **新增 Skill 后**：DeepSeek Provider 的 `allowed_skill_codes` 白名单要同步更新，否则即使被路由到也会被打回

### 古籍 Workflow 旁路

`backend/app/api/v1/routers/passages.py` 中的 `trigger_passage_workflow` 不走 Planner，而是直接构造 `PlannerDecision(target_skill_code="passage_ingestion_workflow", decision_type="workflow")` 提交给 Executor。这是「固定流程」场景的标准模式：当业务不需要意图识别、只是要把已知输入塞进既定 Skill 时，应当沿用此旁路而非滥用 Planner。`ExecutionRun` 的 `trigger_type` 字段（`chat` / `passage_upload` / `passage_manual_input` …）用来区分两条入口，前端在「最近任务」面板里也按它分类。

### 数据库分工

- **SQLite（关系型，由 Alembic 管理）**：`users`、`auth_sessions`、`conversations`、`messages`、`conversation_memories`、`skill_definitions`、`skill_relations`、`planner_decisions`、`execution_runs`、`execution_step_runs`、`passages`
- **Neo4j（图）**：领域知识图谱，schema 来自 `bg_knowledge/数据库存储格式（…）.pdf`：
  - 节点：`Passage_Info`、`Person_Nodes`、`Life_Events`、`Time`、`Location`、`Official_title`、`Historical_Events`
  - 关键 ID 约定：`person_id = doc_id * 1000 + 文章内出现次序`（受文章规模影响，必要时上调倍率）
  - 自动合并键：`Time` 按 `era+year`、`Location` 按 `(dao,fu,zhou,jun,xian)`、`Official_title` / `Historical_Events` 按名称
  - 关键关系：`Person_Nodes -[level=1|2|3]-> Passage_Info`、`Person_Nodes -[生平]-> Life_Events`、`Life_Events -[发生于]-> Time/Location`、`Life_Events -[担任]-> Official_title`（仅 `event_type=任职`）、`Person_Nodes <-[person_relation]-> Person_Nodes`（`F/M/S/D/H/W/Z/C/B/O` 字母序列，长度上限 k=3，超出按 `O` + ≤15 字说明）
- **图操作必须经过 `app.graph.repository.GraphRepository`**：它统一处理 driver 生命周期、参数提取（兼容 `parameters["cypher"]` 占位）和 summary 输出。直接持有 driver 会破坏一致性

## 功能 A 落点（当前开发）

如要把功能 A 端到端落地，整合点和参照来源如下：

1. **入口**：`PassageIngestionWorkflowSkill`（`backend/app/skills/workflow/passage_ingestion_workflow.py`）。当前只串了 `passage_store_sqlite_atomic` 占位 + `passage_store_graph_atomic` 占位，需要把抽取/分级/建图逻辑插进来
2. **抽取流程参考**：`bg_knowledge/整体过程.pdf` 给出了 2.1 提取基本信息（侦察 → prompt 装配 → 抽取整体信息 → 人物间关系）的层级；`bg_knowledge/任务清单与问题讨论.pdf` 给出了完整 prompt 模板（探针扫描、人物特征装配、社会关系映射）。**注意**：CBDB 检索（2.2）与 裁定验证（2.3）属于**功能 B**，功能 A 不要把它们硬编码进来
3. **schema 约束**：`bg_knowledge/数据库存储格式（…）.pdf` 是图节点/关系的唯一权威；`bg_knowledge/年号数据库.pdf` 与 `bg_knowledge/历史事件数据库.pdf` 是 `Time.era` 与 `Historical_Events.event_name` 的对齐表，建议作为常量字典或独立 Skill 加载
4. **人物分级**：依 `bg_knowledge/人物分级指南.pdf`：每篇仅一个 `level=1` 核心人物；`level=2` 重要人物需有强关联（血/地/业缘）+ 具体描述；`level=3` 一般人物只统计数量，不抽取信息
5. **添加新 Skill 时**，必须同时改三处：`registry.py` 的 `_instances`、同文件 `definitions` 列表、以及 DeepSeek Provider 的 `allowed_skill_codes`（如果它需要被 Planner 选中）

## 已知坑位

- 应用启动**不会**自动 `create_all`，必须先 `alembic upgrade head`
- `bcrypt` 必须锁 `4.0.1`，否则 passlib 无法初始化（README 与 `requirements.txt` 已说明）
- `bg_knowledge/`、`.env`、`data/`、`*.sqlite3` 都在 `.gitignore` 内，不要提交
- `register_builtin_metadata()` 在 `ConversationService.__init__` 内被调用——这意味着首次请求会触发写库，单测使用 `DummySession` 是为了绕过这一点
- 现有 `passage_store_graph_atomic` 是占位 Cypher（写入只有注释），任何提到「图入库」「写 Neo4j」的工作都需要把它替换为真实实现
- 当前没有 `python` 命令保证（README §9 注），开发机若仅有 `py`，请用 `py -m uvicorn …`、`py -m pytest …` 等形式调用

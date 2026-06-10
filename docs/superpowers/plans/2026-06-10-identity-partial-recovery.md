# 同名裁定 Partial 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复全文终局裁定的非法下一跳字段、工作流运行状态错记，并安全恢复生产环境 `doc_id=51` 的异常同名裁定。

**Architecture:** 图证据轮次继续使用带 `next_hop_focus` 的现有模型，全文终局改用不含下一跳字段的独立模型并转换为统一内部决定。`Executor` 从工作流输出读取受控状态值。新增命令行脚本通过现有 Atomic Skill 定向重跑单篇文章的同名裁定，不重复功能 A 抽取。

**Tech Stack:** Python 3、FastAPI、Pydantic v2、SQLAlchemy、Neo4j、pytest、Docker Compose

---

## 文件结构

- 修改 `backend/app/skills/atomic/person_identity_resolution.py`：增加全文终局响应模型及转换。
- 修改 `backend/app/agents/executor.py`：统一解析正常返回的运行状态。
- 新增 `backend/scripts/rerun_identity_resolution.py`：定向执行单篇文章的功能 B 同名裁定并回写文章状态。
- 修改 `backend/tests/test_planner_executor.py`：覆盖终局 Schema 和 Executor 状态透传。
- 新增 `backend/tests/test_rerun_identity_resolution.py`：覆盖恢复脚本的状态回写。

### Task 1: 全文终局独立 Schema

**Files:**
- Modify: `backend/app/skills/atomic/person_identity_resolution.py`
- Test: `backend/tests/test_planner_executor.py`

- [ ] **Step 1: 编写失败测试**

在 `test_planner_executor.py` 中模拟 `call_llm_structured`，断言全文终局使用的 Schema 能忽略模型额外返回的自然语言 `next_hop_focus`，并且 `_call_full_text_identity_llm()` 返回的统一决定中 `next_hop_focus == []`。

- [ ] **Step 2: 运行测试确认失败**

Run: `py -m pytest backend/tests/test_planner_executor.py::test_full_text_identity_call_ignores_next_hop_focus -v`

Expected: FAIL，当前全文终局仍使用 `IdentityResolutionDecision`。

- [ ] **Step 3: 实现最小修复**

新增 `FullTextIdentityResolutionDecision`，字段仅包含最终裁定所需内容。让 `_call_full_text_identity_llm()` 使用该 Schema，并返回：

```python
return IdentityResolutionDecision(
    **final_response.model_dump(),
    next_hop_focus=[],
)
```

同时在终局提示词中明确“这是最后一轮，不返回下一跳字段”。

- [ ] **Step 4: 运行相关测试**

Run: `py -m pytest backend/tests/test_planner_executor.py -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/skills/atomic/person_identity_resolution.py backend/tests/test_planner_executor.py
git commit -m "修复同名人物全文终局响应结构"
```

### Task 2: Executor 状态透传

**Files:**
- Modify: `backend/app/agents/executor.py`
- Test: `backend/tests/test_planner_executor.py`

- [ ] **Step 1: 编写失败测试**

新增一个返回 `{"status": "partial"}` 的工作流测试替身，执行后断言对应 `ExecutionRun.status == "partial"`。另加无状态输出测试，断言默认仍为 `success`。

- [ ] **Step 2: 运行测试确认失败**

Run: `py -m pytest backend/tests/test_planner_executor.py::test_executor_persists_workflow_partial_status -v`

Expected: FAIL，当前状态被固定写成 `success`。

- [ ] **Step 3: 实现受控状态解析**

在 Executor 中增加小型辅助函数：

```python
def _resolve_success_status(output: Any) -> str:
    if isinstance(output, dict):
        status = output.get("status")
        if status in {"success", "partial", "skipped"}:
            return status
    return "success"
```

正常执行结束时使用该返回值写入 `execution_run.status`。

- [ ] **Step 4: 运行相关测试**

Run: `py -m pytest backend/tests/test_planner_executor.py -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/agents/executor.py backend/tests/test_planner_executor.py
git commit -m "透传工作流执行状态"
```

### Task 3: 定向恢复脚本

**Files:**
- Create: `backend/scripts/rerun_identity_resolution.py`
- Create: `backend/tests/test_rerun_identity_resolution.py`

- [ ] **Step 1: 编写失败测试**

测试脚本服务函数在裁定输出为 `success` 时更新 `Passage.workflow_status`，输出为 `partial` 时保留 `partial`，异常时回滚且不覆盖原状态。

- [ ] **Step 2: 运行测试确认失败**

Run: `py -m pytest backend/tests/test_rerun_identity_resolution.py -v`

Expected: FAIL，脚本尚不存在。

- [ ] **Step 3: 实现恢复入口**

脚本接受 `--doc-id`，读取文章与执行用户，构造 `ExecutionContext`，通过 `Executor` 调用
`person_identity_resolution_atomic`，触发新的 `ExecutionRun(trigger_type="identity_resolution_rerun")`。
成功后将文章状态更新为输出中的 `success` 或 `partial`；失败时保持原状态并以非零退出码结束。

- [ ] **Step 4: 运行脚本测试**

Run: `py -m pytest backend/tests/test_rerun_identity_resolution.py -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/scripts/rerun_identity_resolution.py backend/tests/test_rerun_identity_resolution.py
git commit -m "新增同名裁定定向恢复脚本"
```

### Task 4: 全量验证与生产恢复

**Files:**
- Verify: `backend/app/skills/atomic/person_identity_resolution.py`
- Verify: `backend/app/agents/executor.py`
- Verify: `backend/scripts/rerun_identity_resolution.py`

- [ ] **Step 1: 运行后端测试**

Run: `py -m pytest backend/tests`

Expected: PASS。

- [ ] **Step 2: 检查代码差异**

Run: `git diff --check HEAD~3..HEAD`

Expected: 无空白错误。

- [ ] **Step 3: 部署后端**

Run: `powershell -ExecutionPolicy Bypass -File .\scripts\deploy-prod.ps1 -Service backend`

Expected: 后端容器重建成功，健康检查返回 `{"status":"ok"}`。

- [ ] **Step 4: 定向重跑**

Run:

```bash
docker exec acgagent-backend python -m scripts.rerun_identity_resolution --doc-id 51
```

Expected: 新运行状态为 `success`，文章状态更新为 `success`。

- [ ] **Step 5: 验证生产数据**

查询 SQLite，确认：

- `passages.doc_id=51` 为 `success`。
- 最新 `identity_resolution_rerun` 为 `success`。
- `51015 ↔ 22030` 有新的合法裁定日志。

查询 Neo4j，确认该人物对 `可能同人.reason` 不包含“重试 3 次仍失败”或“字段校验失败”。

- [ ] **Step 6: 验证标注 API**

调用标注列表接口或直接检查其数据源，确认 `51015 ↔ 22030` 返回的 `reason` 为正常裁定理由。

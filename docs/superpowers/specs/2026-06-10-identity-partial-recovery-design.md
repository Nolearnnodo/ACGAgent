# 同名裁定 Partial 修复设计

## 背景

生产环境文章 `doc_id=51` 的抽取和图入库均已完成，但全文终局同名裁定曾返回
`next_hop_focus=["需要提供原文"]`。现有 `IdentityResolutionDecision` 将该字段限制为
`relations`、`official_titles`、`locations`、`events`，导致结构化校验连续三次失败，
工作流最终被标记为 `partial`。

同时，`Executor` 当前无条件把成功返回的工作流运行记录写为 `success`，造成
`passages.workflow_status=partial` 与 `execution_runs.status=success` 不一致。

## 目标

1. 全文终局裁定不再要求无意义的下一跳字段。
2. 工作流返回 `partial` 时，`ExecutionRun.status` 同步记录为 `partial`。
3. 修复生产环境现有异常裁定，使标注页面不再显示结构化校验错误文本。
4. 保留原始审计记录，不删除历史裁定日志。

## 方案

### 全文终局独立 Schema

新增仅供全文终局使用的响应模型，包含：

- `decision`
- `confidence`
- `positive_evidence`
- `negative_evidence`
- `missing_evidence`
- `reason`

全文终局调用使用该模型，不再接收 `next_hop_focus`。调用完成后转换为现有
`IdentityResolutionDecision`，并固定设置 `next_hop_focus=[]`，保持后续持久化和裁定逻辑兼容。

图证据第 2 至 5 跳继续使用原有 Schema，因为这些轮次仍需要下一跳取证方向。

### 工作流状态透传

`Executor` 在 Skill 正常返回后读取 `output["status"]`：

- 值为 `success`、`partial` 或 `skipped` 时写入对应运行状态。
- 缺失或出现未知值时保持现有 `success` 默认值。
- 异常路径仍写入 `failed`。

文章后台任务继续通过现有 `_resolve_passage_status()` 更新 `Passage.workflow_status`，
两处状态因此保持一致。

### 生产异常数据恢复

部署修复后的后端代码后，定向重新执行 `doc_id=51` 的同名裁定步骤。重跑只处理该文章
当前召回的同名候选，不重新执行功能 A 抽取和整篇图写入。

对 `51015 ↔ 22030`：

- 使用修复后的全文终局 Schema 重新裁定。
- 更新 Neo4j `可能同人` 关系的 `reason`、`confidence` 和证据，不再保留校验异常文本。
- 新增新的 SQLite 裁定日志作为恢复后的审计记录，不删除原失败日志。

重跑无图写入警告后，将 `doc_id=51` 的 `workflow_status` 修正为 `success`。既有
`ExecutionRun id=51` 是历史运行记录，不篡改；恢复操作产生新的运行记录并以真实状态结束。

## 错误处理

- 若终局 LLM 仍无法返回合法 JSON，继续按现有逻辑生成 `insufficient`，并保留 warning。
- 若定向重跑失败，不修改文章现有状态，也不删除已有 `可能同人` 关系。
- 若 Neo4j 关系更新失败，恢复运行标记为 `partial` 或 `failed`，保留错误供排查。

## 测试

1. 全文终局响应即使额外返回自然语言 `next_hop_focus`，独立 Schema 也不会因该字段失败。
2. 转换后的终局决定固定包含 `next_hop_focus=[]`。
3. 工作流输出 `status=partial` 时，`ExecutionRun.status=partial`。
4. 工作流未返回状态时仍记录为 `success`。
5. 现有同名裁定与文章上传测试保持通过。

## 验收标准

- 新上传文章不再因全文终局的 `next_hop_focus` 自然语言内容进入 `partial`。
- `ExecutionRun.status` 与工作流输出状态一致。
- 生产 `doc_id=51` 恢复为 `success`。
- 标注页面中 `51015 ↔ 22030` 不显示“LLM 重试 3 次仍失败”或字段校验错误。
- 原始失败审计日志仍可在 SQLite 中追溯。

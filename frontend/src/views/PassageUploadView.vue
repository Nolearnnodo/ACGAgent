<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  fetchPassageRuns,
  fetchPassageTokenUsage,
  fetchPassageUsageOverview,
  listPassages,
  uploadPassages,
  type PassageExecutionRun,
  type PassageSummary,
  type PassageTokenUsage,
  type PassageUploadResult,
  type PassageUsageOverview,
} from '../api/passages'
import AppLayout from '../layouts/AppLayout.vue'

const STAGES = [
  { skill: 'probe_atomic', label: 'Stage 1 · 探针扫描' },
  { skill: 'passage_meta_atomic', label: 'Stage 2 · 文章基础信息' },
  { skill: 'person_layer_atomic', label: 'Stage 3 · 人物分层与关系候选' },
  { skill: 'event_relation_atomic', label: 'Stage 4 · 事件与关系建图' },
  { skill: 'passage_format_output_atomic', label: 'Stage 5 · 输出抽取文件' },
  { skill: 'person_identity_resolution_atomic', label: 'Stage 6 · 功能 B 同名裁定' },
] as const

type StageStatus = 'pending' | 'running' | 'success' | 'failed'

const selectedFiles = ref<File[]>([])
const uploadedPassages = ref<PassageUploadResult[]>([])
const passages = ref<PassageSummary[]>([])
const activeDocId = ref<number | null>(null)
const activeRuns = ref<PassageExecutionRun[]>([])
const activeUsage = ref<PassageTokenUsage | null>(null)
const usageOverview = ref<PassageUsageOverview | null>(null)
const submitting = ref(false)
const errorMessage = ref<string | null>(null)
const usageError = ref<string | null>(null)
let pollingTimer: number | null = null
let monitorRequestToken = 0

const latestRun = computed<PassageExecutionRun | null>(() => activeRuns.value[0] ?? null)

const latestSummary = computed(() => activeUsage.value?.summary ?? null)

const identityStepOutput = computed<Record<string, unknown> | null>(() => {
  const step = latestRun.value?.steps.find((item) => item.skill_code === 'person_identity_resolution_atomic')
  if (!step?.output_json) return null
  try {
    return JSON.parse(step.output_json) as Record<string, unknown>
  } catch {
    return null
  }
})

const identityStats = computed(() => {
  const data = identityStepOutput.value
  if (!data) return null
  return {
    status: String(data.status ?? 'unknown'),
    candidates: Number(data.candidate_pair_count ?? 0),
    adjudicated: Number(data.adjudicated_pair_count ?? 0),
    merged: Number(data.merged_person_count ?? 0),
    review: Number(data.review_link_count ?? 0),
    failed: Number(data.failed_resolution_count ?? 0),
  }
})

const stageStatuses = computed<StageStatus[]>(() => {
  const run = latestRun.value
  const result: StageStatus[] = STAGES.map(() => 'pending')
  if (!run) return result

  for (const step of run.steps) {
    const idx = step.step_no - 1
    if (idx >= 0 && idx < result.length) {
      result[idx] = step.status as StageStatus
    }
  }
  if (run.status === 'running' || run.status === 'pending') {
    const firstPending = result.findIndex((status) => status === 'pending')
    if (firstPending >= 0) {
      result[firstPending] = 'running'
    }
  }
  return result
})

const outputPath = computed<string | null>(() => {
  const run = latestRun.value
  if (!run || run.status !== 'success') return null
  const last = run.steps.find((step) => step.skill_code === 'passage_format_output_atomic')
  if (!last?.output_json) return null
  try {
    const parsed = JSON.parse(last.output_json)
    return parsed.output_path ?? null
  } catch {
    return null
  }
})

const overallProgressLabel = computed(() => {
  const run = latestRun.value
  if (!run) return ''
  const done = stageStatuses.value.filter((status) => status === 'success').length
  const total = STAGES.length
  if (run.status === 'success') return `已完成 ${total}/${total}`
  if (run.status === 'failed') return `失败于 ${Math.min(done + 1, total)}/${total}`
  return `处理中 ${done}/${total}`
})

function handleFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  selectedFiles.value = Array.from(target.files ?? [])
}

function uploadResultLabel(passage: PassageUploadResult) {
  if (passage.upload_status === 'skipped_existing') {
    return passage.skip_reason ?? '已存在，已跳过'
  }
  return '已加入任务队列'
}

function formatNumber(value: number | null | undefined) {
  return new Intl.NumberFormat('zh-CN').format(value ?? 0)
}

function formatPercent(value: number | null | undefined) {
  return `${((value ?? 0) * 100).toFixed(1)}%`
}

function formatCost(value: number | null | undefined, currency = 'CNY') {
  if (!value) return `0 ${currency}`
  return `${value.toFixed(6)} ${currency}`
}

async function handleUpload() {
  if (!selectedFiles.value.length || submitting.value) return

  submitting.value = true
  errorMessage.value = null
  try {
    uploadedPassages.value = await uploadPassages(selectedFiles.value)
    passages.value = await listPassages()
    if (uploadedPassages.value.length) {
      await openMonitor(uploadedPassages.value[0].doc_id)
    }
    selectedFiles.value = []
  } catch (err: unknown) {
    const detail =
      err && typeof err === 'object' && 'message' in err
        ? String((err as { message?: unknown }).message)
        : '未知错误'
    errorMessage.value = `上传失败：${detail}`
    try {
      passages.value = await listPassages()
    } catch (_e) {
      /* ignore */
    }
  } finally {
    submitting.value = false
  }
}

async function openMonitor(docId: number) {
  activeDocId.value = docId
  activeUsage.value = null
  usageError.value = null
  const token = ++monitorRequestToken
  await refreshMonitor(docId, token)
  startPolling(docId)
}

async function refreshMonitor(docId: number, token: number = monitorRequestToken) {
  const [runs, usageResult] = await Promise.allSettled([
    fetchPassageRuns(docId),
    fetchPassageTokenUsage(docId),
  ])
  if (activeDocId.value !== docId || token !== monitorRequestToken) return

  if (runs.status === 'fulfilled') {
    activeRuns.value = runs.value
  }
  if (usageResult.status === 'fulfilled') {
    activeUsage.value = usageResult.value
    usageError.value = null
  } else {
    usageError.value = '暂未取得 token 追踪数据'
  }
}

function startPolling(docId: number) {
  stopPolling()
  const token = monitorRequestToken
  pollingTimer = window.setInterval(async () => {
    await refreshMonitor(docId, token)
    if (activeDocId.value !== docId || token !== monitorRequestToken) return

    try {
      const nextPassages = await listPassages()
      if (activeDocId.value !== docId || token !== monitorRequestToken) return
      passages.value = nextPassages
      await refreshOverview()
    } catch (_e) {
      /* ignore */
    }

    const run = activeRuns.value[0]
    if (run && ['success', 'failed'].includes(run.status)) {
      stopPolling()
    }
  }, 2000)
}

function stopPolling() {
  if (pollingTimer !== null) {
    window.clearInterval(pollingTimer)
    pollingTimer = null
  }
}

async function refreshOverview() {
  try {
    usageOverview.value = await fetchPassageUsageOverview()
  } catch (_e) {
    /* ignore */
  }
}

onMounted(async () => {
  try {
    passages.value = await listPassages()
  } catch (_e) {
    /* ignore */
  }
  await refreshOverview()
  const inflight = passages.value.find((passage) =>
    ['pending', 'running'].includes(passage.workflow_status),
  )
  if (inflight) {
    await openMonitor(inflight.doc_id)
  } else if (passages.value[0]) {
    await openMonitor(passages.value[0].doc_id)
  }
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <AppLayout>
    <div class="passage-page">
      <section class="passage-card">
        <h3>古籍上传</h3>
        <p>支持 md、txt、docx、pdf、pptx、xlsx、html 等格式。每个文件会触发一次固定 workflow。</p>

        <input
          type="file"
          multiple
          :disabled="submitting"
          accept=".md,.markdown,.txt,.docx,.doc,.pdf,.pptx,.ppt,.xlsx,.xls,.csv,.html,.htm,.epub"
          @change="handleFileChange"
        />
        <button type="button" :disabled="submitting || !selectedFiles.length" @click="handleUpload">
          {{ submitting ? '上传中，请勿重复点击' : '开始上传' }}
        </button>

        <p v-if="errorMessage" class="passage-error">{{ errorMessage }}</p>

        <ul class="passage-file-list">
          <li v-for="file in selectedFiles" :key="file.name">{{ file.name }}</li>
        </ul>
      </section>

      <section class="passage-card passage-card--scrollable">
        <h3>上传结果与最近文章</h3>
        <div class="passage-scroll-area">
          <div v-if="uploadedPassages.length" class="passage-result-list">
            <article
              v-for="passage in uploadedPassages"
              :key="passage.doc_id"
              class="passage-result-item"
              @click="openMonitor(passage.doc_id)"
            >
              <strong>{{ passage.title }}</strong>
              <span>{{ uploadResultLabel(passage) }}</span>
            </article>
          </div>
          <div v-if="passages.length" class="passage-monitor-list">
            <button
              v-for="passage in passages"
              :key="passage.doc_id"
              class="passage-monitor-button"
              :class="{ 'passage-monitor-button--active': passage.doc_id === activeDocId }"
              type="button"
              @click="openMonitor(passage.doc_id)"
            >
              <strong>{{ passage.title }}</strong>
              <span>{{ passage.workflow_status }}</span>
            </button>
          </div>
        </div>
      </section>

      <section class="passage-card passage-card--monitor">
        <h3>任务监控</h3>
        <p v-if="!latestRun" class="passage-hint">请选择一篇文章查看 workflow、功能 B 与 token 追踪。</p>

        <div v-else class="passage-progress">
          <div class="passage-progress__header">
            <strong>Run #{{ latestRun.id }}</strong>
            <span :class="`passage-status passage-status--${latestRun.status}`">
              {{ latestRun.status }}
            </span>
          </div>
          <p class="passage-progress__sub">{{ overallProgressLabel }}</p>

          <div class="trace-panel">
            <div class="trace-panel__head">
              <strong>资源追踪</strong>
              <span v-if="latestSummary">LLM 调用 {{ latestSummary.llm_call_count }} 次</span>
              <span v-else>{{ usageError ?? '等待 trace 数据' }}</span>
            </div>
            <div class="trace-metrics">
              <div>
                <span>Total tokens</span>
                <strong>{{ formatNumber(latestSummary?.total_tokens) }}</strong>
              </div>
              <div>
                <span>缓存命中率</span>
                <strong>{{ formatPercent(latestSummary?.cache_hit_ratio) }}</strong>
              </div>
              <div>
                <span>Hit / Miss</span>
                <strong>
                  {{ formatNumber(latestSummary?.prompt_cache_hit_tokens) }} /
                  {{ formatNumber(latestSummary?.prompt_cache_miss_tokens) }}
                </strong>
              </div>
              <div>
                <span>估算费用</span>
                <strong>{{ formatCost(latestSummary?.estimated_total_cost, latestSummary?.currency) }}</strong>
              </div>
            </div>
          </div>

          <div v-if="identityStats" class="identity-panel">
            <div class="trace-panel__head">
              <strong>功能 B 同名裁定</strong>
              <span>{{ identityStats.status }}</span>
            </div>
            <div class="trace-metrics trace-metrics--compact">
              <div>
                <span>候选</span>
                <strong>{{ identityStats.candidates }}</strong>
              </div>
              <div>
                <span>已裁定</span>
                <strong>{{ identityStats.adjudicated }}</strong>
              </div>
              <div>
                <span>合并</span>
                <strong>{{ identityStats.merged }}</strong>
              </div>
              <div>
                <span>待复核</span>
                <strong>{{ identityStats.review }}</strong>
              </div>
              <div>
                <span>失败</span>
                <strong>{{ identityStats.failed }}</strong>
              </div>
            </div>
          </div>

          <ol class="passage-stage-list">
            <li
              v-for="(stage, idx) in STAGES"
              :key="stage.skill"
              class="passage-stage"
              :class="`passage-stage--${stageStatuses[idx]}`"
            >
              <span class="passage-stage__icon">
                <template v-if="stageStatuses[idx] === 'success'">OK</template>
                <template v-else-if="stageStatuses[idx] === 'failed'">!</template>
                <template v-else-if="stageStatuses[idx] === 'running'">...</template>
                <template v-else>{{ idx + 1 }}</template>
              </span>
              <span class="passage-stage__label">{{ stage.label }}</span>
            </li>
          </ol>

          <div v-if="outputPath" class="passage-output">
            <strong>输出文件：</strong>
            <code>{{ outputPath }}</code>
            <p class="passage-output__hint">
              容器内路径已映射到宿主机 <code>./output/{{ outputPath.split('/').pop() }}</code>
            </p>
          </div>
        </div>
      </section>

      <section v-if="usageOverview && usageOverview.items.length" class="passage-card passage-card--overview">
        <h3>全部古籍资源追踪</h3>
        <div class="overview-totals">
          <div>
            <span>总 LLM 调用</span>
            <strong>{{ formatNumber(usageOverview.total_llm_calls) }}</strong>
          </div>
          <div>
            <span>总 Tokens</span>
            <strong>{{ formatNumber(usageOverview.total_tokens) }}</strong>
          </div>
          <div>
            <span>总费用</span>
            <strong>{{ formatCost(usageOverview.total_cost, usageOverview.currency) }}</strong>
          </div>
        </div>
        <div class="overview-table-wrap">
          <table class="overview-table">
            <thead>
              <tr>
                <th>文章</th>
                <th>状态</th>
                <th>LLM 调用</th>
                <th>Tokens</th>
                <th>缓存命中</th>
                <th>费用</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in usageOverview.items"
                :key="item.doc_id"
                class="overview-row"
                :class="{ 'overview-row--active': item.doc_id === activeDocId }"
                @click="openMonitor(item.doc_id)"
              >
                <td class="overview-title">{{ item.title }}</td>
                <td>
                  <span :class="`passage-status passage-status--${item.workflow_status}`">
                    {{ item.workflow_status }}
                  </span>
                </td>
                <td>{{ item.llm_call_count }}</td>
                <td>{{ formatNumber(item.total_tokens) }}</td>
                <td>{{ formatPercent(item.cache_hit_ratio) }}</td>
                <td>{{ formatCost(item.estimated_total_cost, item.currency) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </AppLayout>
</template>

<style scoped>
.passage-page {
  display: grid;
  grid-template-columns: minmax(260px, 0.85fr) minmax(280px, 1fr) minmax(420px, 1.35fr);
  gap: 24px;
}

.passage-card--scrollable {
  max-height: 70vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.passage-scroll-area {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  padding-right: 4px;
}

.passage-scroll-area::-webkit-scrollbar {
  width: 6px;
}

.passage-scroll-area::-webkit-scrollbar-thumb {
  background: #c4d0e6;
  border-radius: 3px;
}

.passage-card--overview {
  grid-column: 1 / -1;
}

.passage-card {
  background: #fff;
  border-radius: 16px;
  padding: 24px;
  box-shadow: 0 14px 36px rgba(115, 137, 177, 0.12);
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.passage-card--monitor {
  gap: 18px;
}

.passage-card h3 {
  margin: 0;
  color: #31456f;
}

.passage-card p {
  margin: 0;
  color: #7183a8;
}

.passage-card button {
  width: fit-content;
  border: none;
  border-radius: 10px;
  padding: 10px 16px;
  background: #2f6fed;
  color: #fff;
  cursor: pointer;
}

.passage-card button:disabled {
  background: #b6c4e3;
  cursor: not-allowed;
}

.passage-file-list,
.passage-result-list {
  margin: 0;
  padding-left: 18px;
}

.passage-result-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid #eef2f8;
  cursor: pointer;
}

.passage-monitor-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.passage-monitor-button {
  width: 100%;
  text-align: left;
  border: 1px solid #e4ebf7;
  border-radius: 12px;
  background: #fafcff;
  padding: 14px;
  cursor: pointer;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
}

.passage-monitor-button strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.passage-monitor-button--active {
  border-color: #8fb3ff;
  background: #eef5ff;
}

.passage-progress__header,
.trace-panel__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.passage-progress__sub {
  margin: 4px 0 12px !important;
  color: #586a8e;
  font-size: 14px;
}

.passage-status {
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
}

.passage-status--success {
  background: #e2f7e8;
  color: #2c9e4d;
}

.passage-status--failed {
  background: #fde2e2;
  color: #c44545;
}

.passage-status--running,
.passage-status--pending {
  background: #e6efff;
  color: #2f6fed;
}

.trace-panel,
.identity-panel {
  border: 1px solid #e3eaf6;
  border-radius: 12px;
  padding: 14px;
  background: #fafcff;
}

.identity-panel {
  margin-top: 12px;
  background: #fbfcf8;
  border-color: #dfe8c8;
}

.trace-panel__head span {
  color: #7183a8;
  font-size: 13px;
}

.trace-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.trace-metrics--compact {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

.trace-metrics div {
  min-height: 64px;
  border-radius: 10px;
  background: #fff;
  border: 1px solid #edf2fa;
  padding: 10px;
}

.trace-metrics span {
  display: block;
  color: #7183a8;
  font-size: 12px;
  margin-bottom: 6px;
}

.trace-metrics strong {
  color: #31456f;
  font-size: 16px;
  line-height: 1.25;
  word-break: break-word;
}

.passage-stage-list {
  list-style: none;
  margin: 16px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.passage-stage {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 12px;
  border: 1px solid #e3eaf6;
  background: #fafcff;
  color: #506287;
  transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease;
}

.passage-stage--running {
  border-color: #8fb3ff;
  background: #eef5ff;
  color: #2f6fed;
}

.passage-stage--success {
  border-color: #b6e3c7;
  background: #f0fbf4;
  color: #2c9e4d;
}

.passage-stage--failed {
  border-color: #f4c1c1;
  background: #fff1f1;
  color: #b54040;
}

.passage-stage__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 28px;
  border-radius: 999px;
  font-weight: 700;
  font-size: 12px;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid currentColor;
  flex: 0 0 auto;
}

.passage-stage__label {
  flex: 1;
  font-size: 14px;
}

.passage-output {
  margin-top: 18px;
  padding: 14px;
  background: #f5fbf7;
  border: 1px solid #c4e3cf;
  border-radius: 12px;
  color: #2c9e4d;
}

.passage-output code {
  display: inline-block;
  padding: 2px 6px;
  background: #fff;
  border-radius: 6px;
  border: 1px solid #d6e8de;
  margin: 0 2px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 13px;
  color: #2c4d39;
  word-break: break-all;
}

.passage-output__hint {
  margin: 8px 0 0 !important;
  font-size: 12px;
  color: #5a8a6c;
}

.passage-hint {
  color: #93a1be;
}

.passage-error {
  margin: 0;
  padding: 10px 14px;
  background: #fff1f1;
  border: 1px solid #f4c1c1;
  border-radius: 10px;
  color: #b54040;
}

.overview-totals {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.overview-totals div {
  border-radius: 10px;
  background: #f5f8ff;
  border: 1px solid #e3eaf6;
  padding: 12px;
}

.overview-totals span {
  display: block;
  color: #7183a8;
  font-size: 12px;
  margin-bottom: 4px;
}

.overview-totals strong {
  color: #31456f;
  font-size: 18px;
}

.overview-table-wrap {
  overflow-x: auto;
}

.overview-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.overview-table th {
  text-align: left;
  padding: 10px 12px;
  color: #7183a8;
  font-weight: 600;
  border-bottom: 2px solid #e3eaf6;
  white-space: nowrap;
}

.overview-table td {
  padding: 10px 12px;
  border-bottom: 1px solid #eef2f8;
  white-space: nowrap;
}

.overview-title {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-row {
  cursor: pointer;
  transition: background 0.15s;
}

.overview-row:hover {
  background: #f5f8ff;
}

.overview-row--active {
  background: #eef5ff;
}

@media (max-width: 1200px) {
  .passage-page {
    grid-template-columns: 1fr;
  }

  .passage-card--overview {
    grid-column: 1;
  }

  .trace-metrics,
  .trace-metrics--compact {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .trace-metrics,
  .trace-metrics--compact {
    grid-template-columns: 1fr;
  }

  .overview-totals {
    grid-template-columns: 1fr;
  }
}
</style>

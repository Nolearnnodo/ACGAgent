<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import AppLayout from '../layouts/AppLayout.vue'
import {
  fetchPassageRuns,
  listPassages,
  uploadPassages,
  type PassageExecutionRun,
  type PassageSummary,
  type PassageUploadResult,
} from '../api/passages'

// Stage 顺序与后端 PassageIngestionWorkflowSkill.STAGES 严格对齐
const STAGES = [
  { skill: 'probe_atomic', label: 'Stage 1 · 探针扫描（性别 / 残缺 / 释道 / 字号）' },
  { skill: 'passage_meta_atomic', label: 'Stage 2 · 文章基础信息（年号 / 朝代 / source_type）' },
  { skill: 'person_layer_atomic', label: 'Stage 3 · 人物层（扫描 + 重要度评级 + 关系候选）' },
  { skill: 'event_relation_atomic', label: 'Stage 4 · 事件 + 历史关联 + 关系字母码' },
  { skill: 'passage_format_output_atomic', label: 'Stage 5 · 渲染 YAML 输出文件' },
  { skill: 'person_exact_match_merge_atomic', label: 'Stage 6 · 同名人物精确匹配合并' },
] as const

const selectedFiles = ref<File[]>([])
const uploadedPassages = ref<PassageUploadResult[]>([])
const passages = ref<PassageSummary[]>([])
const activeDocId = ref<number | null>(null)
const activeRuns = ref<PassageExecutionRun[]>([])
const submitting = ref(false)
const errorMessage = ref<string | null>(null)
let pollingTimer: number | null = null
let monitorRequestToken = 0

const latestRun = computed<PassageExecutionRun | null>(() =>
  activeRuns.value[0] ?? null,
)

// 用 stage_no（1..6）作为索引，标记当前每个 stage 的状态
type StageStatus = 'pending' | 'running' | 'success' | 'failed'

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
  // 第一个 pending 项视为正在运行（仅当 run 整体未结束）
  if (run.status === 'running' || run.status === 'pending') {
    const firstPending = result.findIndex((s) => s === 'pending')
    if (firstPending >= 0) {
      result[firstPending] = 'running'
    }
  }
  return result
})

const outputPath = computed<string | null>(() => {
  const run = latestRun.value
  if (!run || run.status !== 'success') return null
  const last = run.steps.find((s) => s.skill_code === 'passage_format_output_atomic')
  if (!last || !last.output_json) return null
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
  const done = stageStatuses.value.filter((s) => s === 'success').length
  const total = STAGES.length
  if (run.status === 'success') return `已完成 ${total}/${total}`
  if (run.status === 'failed') return `失败于 ${done + 1}/${total}`
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

async function handleUpload() {
  if (!selectedFiles.value.length || submitting.value) {
    return
  }

  submitting.value = true
  errorMessage.value = null
  try {
    // 后端立即返回 pending；workflow 在 BackgroundTasks 里跑
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
  const token = ++monitorRequestToken
  await refreshRuns(docId, token)
  startPolling(docId)
}

async function refreshRuns(docId: number, token: number = monitorRequestToken) {
  const runs = await fetchPassageRuns(docId)
  if (activeDocId.value !== docId || token !== monitorRequestToken) {
    return
  }
  activeRuns.value = runs
}

function startPolling(docId: number) {
  stopPolling()
  const token = monitorRequestToken
  pollingTimer = window.setInterval(async () => {
    await refreshRuns(docId, token)
    if (activeDocId.value !== docId || token !== monitorRequestToken) {
      return
    }
    try {
      const nextPassages = await listPassages()
      if (activeDocId.value !== docId || token !== monitorRequestToken) {
        return
      }
      passages.value = nextPassages
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

onMounted(async () => {
  try {
    passages.value = await listPassages()
  } catch (_e) {
    /* ignore */
  }
  const inflight = passages.value.find((p) =>
    ['pending', 'running'].includes(p.workflow_status),
  )
  if (inflight) {
    await openMonitor(inflight.doc_id)
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
        <p>
          支持 .md / .txt / .docx / .pdf / .pptx / .xlsx / .html 等格式（由 markitdown 统一转 markdown）。
          每文件触发一次 workflow，约 30 秒。
        </p>

        <input
          type="file"
          multiple
          :disabled="submitting"
          accept=".md,.markdown,.txt,.docx,.doc,.pdf,.pptx,.ppt,.xlsx,.xls,.csv,.html,.htm,.epub"
          @change="handleFileChange"
        />
        <button type="button" :disabled="submitting || !selectedFiles.length" @click="handleUpload">
          {{ submitting ? '上传中...（请勿重复点击）' : '开始上传' }}
        </button>

        <p v-if="errorMessage" class="passage-error">{{ errorMessage }}</p>

        <ul class="passage-file-list">
          <li v-for="file in selectedFiles" :key="file.name">{{ file.name }}</li>
        </ul>
      </section>

      <section class="passage-card">
        <h3>上传结果与最近文章</h3>
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
      </section>

      <section class="passage-card">
        <h3>任务监控</h3>
        <p v-if="!latestRun" class="passage-hint">请选择一篇文章查看 workflow 状态。</p>

        <div v-else class="passage-progress">
          <div class="passage-progress__header">
            <strong>Run #{{ latestRun.id }}</strong>
            <span :class="`passage-status passage-status--${latestRun.status}`">
              {{ latestRun.status }}
            </span>
          </div>
          <p class="passage-progress__sub">{{ overallProgressLabel }}</p>

          <ol class="passage-stage-list">
            <li
              v-for="(stage, idx) in STAGES"
              :key="stage.skill"
              class="passage-stage"
              :class="`passage-stage--${stageStatuses[idx]}`"
            >
              <span class="passage-stage__icon">
                <template v-if="stageStatuses[idx] === 'success'">✓</template>
                <template v-else-if="stageStatuses[idx] === 'failed'">✗</template>
                <template v-else-if="stageStatuses[idx] === 'running'">…</template>
                <template v-else>{{ idx + 1 }}</template>
              </span>
              <span class="passage-stage__label">{{ stage.label }}</span>
            </li>
          </ol>

          <div v-if="outputPath" class="passage-output">
            <strong>输出文件：</strong>
            <code>{{ outputPath }}</code>
            <p class="passage-output__hint">
              该路径是容器内地址；宿主机上同步映射为
              <code>./output/{{ outputPath.split('/').pop() }}</code>
            </p>
          </div>
        </div>
      </section>
    </div>
  </AppLayout>
</template>

<style scoped>
.passage-page {
  display: grid;
  grid-template-columns: 1fr 1fr 1.2fr;
  gap: 24px;
}

.passage-card {
  background: #fff;
  border-radius: 20px;
  padding: 24px;
  box-shadow: 0 14px 36px rgba(115, 137, 177, 0.12);
  display: flex;
  flex-direction: column;
  gap: 16px;
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
  border-radius: 12px;
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
  text-align: left;
  border: 1px solid #e4ebf7;
  border-radius: 14px;
  background: #fafcff;
  padding: 14px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.passage-monitor-button--active {
  border-color: #8fb3ff;
  background: #eef5ff;
}

.passage-progress__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
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

.passage-stage-list {
  list-style: none;
  margin: 0;
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
  transition: all 0.2s ease;
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
  width: 28px;
  height: 28px;
  border-radius: 50%;
  font-weight: 700;
  font-size: 14px;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid currentColor;
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
</style>

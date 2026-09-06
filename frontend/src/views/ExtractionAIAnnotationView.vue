<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { getApiErrorMessage } from '../api/client'
import {
  type AIAnnotationConfig,
  type AIAnnotationJob,
  type AIAnnotationPrompt,
  type AIAnnotationPromptOverrides,
  type AIAnnotationResult,
  type AIAnnotationTaskContext,
  fetchAIAnnotationTask,
  generateAIAnnotation,
  enqueueAIAnnotationRepair,
  listAIAnnotationJobs,
  previewAIAnnotationPrompt,
  validateAIAnnotation,
} from '../api/aiAnnotations'
import {
  type ExtractionTaskSummary,
  importExtractionDraft,
  listExtractionTasks,
} from '../api/extractionAnnotations'
import AppLayout from '../layouts/AppLayout.vue'
import { useAuthStore } from '../stores/auth'

const CONFIG_STORAGE_KEY = 'acgagent:ai-annotation-config'
const authStore = useAuthStore()

const defaultConfig: AIAnnotationConfig = {
  provider: '',
  api_key: '',
  base_url: '',
  model: '',
  temperature: null,
  extra_instruction: '',
}

const tasks = ref<ExtractionTaskSummary[]>([])
const selectedTaskId = ref<number | null>(null)
const taskContext = ref<AIAnnotationTaskContext | null>(null)
const result = ref<AIAnnotationResult | null>(null)
const rawJson = ref('')
const jobsByTaskId = ref<Record<number, AIAnnotationJob>>({})
const draftsByTaskId = ref<Record<number, { result: AIAnnotationResult | null; rawJson: string }>>({})
const appliedJobKeysByTaskId = ref<Record<number, string>>({})
const config = ref<AIAnnotationConfig>({ ...defaultConfig })
const searchQuery = ref('')
const tasksLoading = ref(false)
const contextLoading = ref(false)
const generating = ref(false)
const validating = ref(false)
const repairing = ref(false)
const importing = ref(false)
const feedback = ref('')
const configSaved = ref(false)
const promptOpen = ref(false)
const promptLoading = ref(false)
const promptPreview = ref<AIAnnotationPrompt | null>(null)
let jobPollingTimer: number | null = null
let jobPollingInFlight = false
let contextRequestToken = 0

const selectedTask = computed(() => (
  tasks.value.find((task) => task.id === selectedTaskId.value) ?? null
))

const selectedJob = computed(() => (
  selectedTaskId.value === null ? null : jobsByTaskId.value[selectedTaskId.value] ?? null
))

const filteredTasks = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  if (!keyword) return tasks.value
  return tasks.value.filter((task) => (
    task.passage_title.toLowerCase().includes(keyword)
    || String(task.id).includes(keyword)
    || String(task.passage_id).includes(keyword)
  ))
})

const promptReady = computed(() => (
  !promptPreview.value
  || Boolean(
    promptPreview.value.system_prompt.trim()
    && promptPreview.value.user_prompt.trim(),
  )
))

const selectedJobBusy = computed(() => (
  selectedJob.value?.status === 'queued' || selectedJob.value?.status === 'running'
))

const canGenerate = computed(() => Boolean(
  selectedTask.value
  && taskContext.value
  && promptReady.value
  && !generating.value
  && !repairing.value
  && !selectedJobBusy.value,
))

const canImport = computed(() => Boolean(
  selectedTask.value
  && selectedTask.value.submission_id
  && selectedTask.value.submission_state === 'draft'
  && selectedTask.value.revision !== null
  && result.value?.document
  && !repairing.value
  && !importing.value,
))

const canRepair = computed(() => Boolean(
  selectedTask.value
  && taskContext.value
  && rawJson.value.trim()
  && result.value
  && result.value.validation_status !== 'valid'
  && !repairing.value
  && !validating.value
  && !generating.value
  && !selectedJobBusy.value,
))

const contextLength = computed(() => taskContext.value?.passage.context.length ?? 0)

function normalizeTemperature(value: unknown): number | null {
  if (value === '' || value === null || value === undefined) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function requestConfig(): AIAnnotationConfig {
  return {
    provider: config.value.provider,
    api_key: config.value.api_key,
    base_url: config.value.base_url,
    model: config.value.model,
    temperature: normalizeTemperature(config.value.temperature),
    extra_instruction: config.value.extra_instruction,
  }
}

function loadConfig() {
  try {
    const stored = window.localStorage.getItem(CONFIG_STORAGE_KEY)
    if (!stored) return
    const parsed = JSON.parse(stored) as Partial<AIAnnotationConfig>
    config.value = {
      ...defaultConfig,
      ...parsed,
      temperature: normalizeTemperature(parsed.temperature),
      // API Key 只在当前页面会话中使用，不写入 localStorage。
      api_key: '',
    }
  } catch {
    config.value = { ...defaultConfig }
  }
}

function saveConfig() {
  const { api_key: _apiKey, ...safeConfig } = requestConfig()
  window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(safeConfig))
  configSaved.value = true
  window.setTimeout(() => {
    configSaved.value = false
  }, 1800)
}

async function loadTasks() {
  tasksLoading.value = true
  feedback.value = ''
  try {
    const [nextTasks, nextJobs] = await Promise.all([
      listExtractionTasks(),
      listAIAnnotationJobs(),
    ])
    tasks.value = nextTasks
    replaceJobs(nextJobs)
    const currentStillExists = nextTasks.some((task) => task.id === selectedTaskId.value)
    if (!currentStillExists && nextTasks.length) {
      await openTask(nextTasks[0])
    } else if (!nextTasks.length) {
      selectedTaskId.value = null
      taskContext.value = null
      result.value = null
      rawJson.value = ''
    }
  } catch (error) {
    feedback.value = getApiErrorMessage(error, 'AI 标注任务列表加载失败。')
  } finally {
    tasksLoading.value = false
  }
}

function jobKey(job: AIAnnotationJob) {
  return `${job.id}:${job.status}:${job.finished_at ?? ''}`
}

function saveTaskDraft(taskId: number) {
  draftsByTaskId.value = {
    ...draftsByTaskId.value,
    [taskId]: {
      result: result.value,
      rawJson: rawJson.value,
    },
  }
}

function saveCurrentDraft() {
  if (selectedTaskId.value === null) return
  saveTaskDraft(selectedTaskId.value)
}

function restoreTaskDraft(taskId: number) {
  const draft = draftsByTaskId.value[taskId]
  if (draft) {
    result.value = draft.result
    rawJson.value = draft.rawJson
    return
  }

  const job = jobsByTaskId.value[taskId]
  const persistedResult = job?.saved_result
    ?? (job?.status === 'success' ? job.result : null)
  if (persistedResult) {
    result.value = persistedResult
    rawJson.value = persistedResult.document
      ? stringifyOutput(persistedResult.document)
      : persistedResult.raw_content
    saveTaskDraft(taskId)
    return
  }

  result.value = null
  rawJson.value = ''
}

function syncSelectedJob() {
  const taskId = selectedTaskId.value
  if (taskId === null) return
  const job = jobsByTaskId.value[taskId]
  if (!job) return

  const key = jobKey(job)
  if (appliedJobKeysByTaskId.value[taskId] === key) return

  const persistedResult = job.saved_result ?? job.result
  if (job.status === 'success' && persistedResult) {
    result.value = persistedResult
    rawJson.value = persistedResult.document
      ? stringifyOutput(persistedResult.document)
      : persistedResult.raw_content
    saveTaskDraft(taskId)
    feedback.value = persistedResult.validation_status === 'valid'
      ? 'AI 标注已生成并通过校验。'
      : 'AI 标注已返回，请根据校验提示修订输出。'
  } else if (job.status === 'failed') {
    feedback.value = job.error_message || 'AI 标注任务失败，请重新发送。'
  }

  appliedJobKeysByTaskId.value = {
    ...appliedJobKeysByTaskId.value,
    [taskId]: key,
  }
}

function hasActiveJobs() {
  return Object.values(jobsByTaskId.value).some((job) => (
    job.status === 'queued' || job.status === 'running'
  ))
}

function stopJobPolling() {
  if (jobPollingTimer !== null) {
    window.clearInterval(jobPollingTimer)
    jobPollingTimer = null
  }
}

function startJobPolling() {
  if (jobPollingTimer !== null || !hasActiveJobs()) return
  jobPollingTimer = window.setInterval(() => {
    void pollJobs()
  }, 2000)
}

function replaceJobs(nextJobs: AIAnnotationJob[]) {
  const nextByTaskId: Record<number, AIAnnotationJob> = {}
  for (const job of nextJobs) nextByTaskId[job.task_id] = job
  jobsByTaskId.value = nextByTaskId
  syncSelectedJob()
  if (hasActiveJobs()) startJobPolling()
  else stopJobPolling()
}

async function pollJobs() {
  if (jobPollingInFlight) return
  jobPollingInFlight = true
  try {
    replaceJobs(await listAIAnnotationJobs())
  } catch {
    // 轮询失败不清空当前篇目的结果，下一轮继续尝试。
  } finally {
    jobPollingInFlight = false
  }
}

async function openTask(task: ExtractionTaskSummary) {
  const requestToken = ++contextRequestToken
  saveCurrentDraft()
  selectedTaskId.value = task.id
  taskContext.value = null
  restoreTaskDraft(task.id)
  promptOpen.value = false
  promptPreview.value = null
  promptLoading.value = false
  feedback.value = ''
  syncSelectedJob()
  contextLoading.value = true
  try {
    const nextContext = await fetchAIAnnotationTask(task.id)
    if (requestToken === contextRequestToken && selectedTaskId.value === task.id) {
      taskContext.value = nextContext
    }
  } catch (error) {
    if (requestToken === contextRequestToken && selectedTaskId.value === task.id) {
      feedback.value = getApiErrorMessage(error, '篇目正文加载失败。')
    }
  } finally {
    if (requestToken === contextRequestToken) contextLoading.value = false
  }
}

function statusLabel(status: string) {
  if (status === 'ready_for_adjudication') return '待复核'
  if (status === 'in_progress') return '标注中'
  if (status === 'completed') return '已完成'
  return '待领取'
}

function submissionLabel(task: ExtractionTaskSummary) {
  if (task.submission_state === 'submitted') return '我的标注已提交'
  if (task.submission_state === 'draft') return '我已领取'
  if (task.available_slots > 0) return `可领取 ${task.available_slots} 槽`
  return '槽位已满'
}

function aiJobOperationLabel(job?: AIAnnotationJob | null) {
  return job?.operation === 'repair' ? 'API 修复' : '标注生成'
}

function aiJobStatusLabel(job?: AIAnnotationJob | null) {
  if (!job) return '未生成'
  if (job.status === 'queued') return `${aiJobOperationLabel(job)}排队中`
  if (job.status === 'running') return `${aiJobOperationLabel(job)}中`
  if (job.status === 'failed') return `${aiJobOperationLabel(job)}失败`
  return (job.saved_result ?? job.result)?.validation_status === 'valid'
    ? `${aiJobOperationLabel(job)}完成`
    : '待校验'
}

function aiJobStatusHint(job?: AIAnnotationJob | null) {
  if (!job) return '尚未为当前篇目发送 AI 标注请求。'
  if (job.status === 'queued') return `${aiJobOperationLabel(job)}请求已加入生成与修复共享队列，正在等待可用的并发槽位。`
  if (job.status === 'running') return `后台正在执行${aiJobOperationLabel(job)}；切换篇目不会影响这条任务。`
  if (job.status === 'failed') return job.error_message || '后台任务失败，请检查配置后重新发送。'
  return (job.saved_result ?? job.result)?.validation_status === 'valid'
    ? '结果已保存到服务器的当前篇目记录，可继续编辑、下载或导入。'
    : '结果已保存到服务器的当前篇目记录，请先修订并重新校验 JSON。'
}

function sourceTypeLabel(sourceType: string) {
  if (sourceType === 'upload') return '文件上传'
  if (sourceType === 'manual_input') return '手工录入'
  return sourceType || '未知来源'
}

function digestPreview(value: string) {
  return value ? `${value.slice(0, 10)}…${value.slice(-8)}` : '—'
}

function resultStatusLabel(status?: AIAnnotationResult['validation_status']) {
  if (status === 'valid') return '格式与规则通过'
  if (status === 'invalid_rules') return 'JSON 合法，但规则未通过'
  if (status === 'invalid_format') return 'JSON / Schema 未通过'
  return '等待生成'
}

function resultStatusHint(status?: AIAnnotationResult['validation_status']) {
  if (status === 'valid') return '可以下载，也可以导入当前篇目的独立盲标草稿。'
  if (status === 'invalid_rules') return '仍可导入独立盲标草稿；约束错误会保留，等待人工修改，也可以调用 API 修复问题。'
  if (status === 'invalid_format') return '只有无法解析为 JSON 时不能导入；请先修正 JSON 结构或调用 API 修复问题。'
  return '选择左侧篇目后填写调用配置。'
}

function stringifyOutput(value: unknown) {
  return JSON.stringify(value, null, 2)
}

async function loadPrompt() {
  if (!selectedTask.value || !taskContext.value) return
  promptLoading.value = true
  try {
    promptPreview.value = await previewAIAnnotationPrompt(
      selectedTask.value.id,
      requestConfig(),
    )
  } catch (error) {
    feedback.value = getApiErrorMessage(error, '提示词预览加载失败。')
  } finally {
    promptLoading.value = false
  }
}

function activePromptOverrides(): AIAnnotationPromptOverrides | undefined {
  if (!promptPreview.value) return undefined
  return {
    system_prompt: promptPreview.value.system_prompt,
    user_prompt: promptPreview.value.user_prompt,
  }
}

async function togglePrompt() {
  promptOpen.value = !promptOpen.value
  if (promptOpen.value) await loadPrompt()
}

async function generate() {
  if (!selectedTask.value || !canGenerate.value) return
  const clientStartedAt = new Date().toISOString()
  saveCurrentDraft()
  // 当前篇目的上一轮结果已经保存在 draftsByTaskId 中；本轮请求开始后先清空可见结果，
  // 避免“本轮模型调用失败”与“上一轮待校验”同时显示造成误判。
  result.value = null
  rawJson.value = ''
  generating.value = true
  feedback.value = ''
  try {
    const next = await generateAIAnnotation(
      selectedTask.value.id,
      requestConfig(),
      activePromptOverrides(),
      clientStartedAt,
    )
    jobsByTaskId.value = {
      ...jobsByTaskId.value,
      [next.task_id]: next,
    }
    syncSelectedJob()
    if (hasActiveJobs()) startJobPolling()
    saveConfig()
    feedback.value = next.status === 'failed'
      ? (next.error_message || 'AI 标注任务提交失败。')
      : `AI 标注已${aiJobStatusLabel(next)}，完成后会自动回填当前篇目。`
  } catch (error) {
    feedback.value = getApiErrorMessage(error, 'AI 标注调用失败。')
  } finally {
    generating.value = false
  }
}

async function validateOutput() {
  if (!selectedTask.value || !rawJson.value.trim() || validating.value || repairing.value) return
  validating.value = true
  feedback.value = ''
  try {
    const next = await validateAIAnnotation(
      selectedTask.value.id,
      rawJson.value,
      selectedJob.value?.id,
    )
    result.value = next
    if (next.document) rawJson.value = stringifyOutput(next.document)
    if (selectedJob.value) {
      jobsByTaskId.value = {
        ...jobsByTaskId.value,
        [selectedTask.value.id]: {
          ...selectedJob.value,
          saved_result: next,
        },
      }
    }
    saveCurrentDraft()
    feedback.value = next.validation_status === 'valid'
      ? '当前 JSON 已通过格式和标注规则校验。'
      : '当前 JSON 尚未通过全部校验，请查看问题列表。'
  } catch (error) {
    feedback.value = getApiErrorMessage(error, 'JSON 校验请求失败。')
  } finally {
    validating.value = false
  }
}

async function repairOutput() {
  if (!selectedTask.value || !canRepair.value) return
  const clientStartedAt = new Date().toISOString()
  saveCurrentDraft()
  repairing.value = true
  feedback.value = ''
  try {
    const next = await enqueueAIAnnotationRepair(
      selectedTask.value.id,
      rawJson.value,
      requestConfig(),
      selectedJob.value?.id,
      clientStartedAt,
    )
    jobsByTaskId.value = {
      ...jobsByTaskId.value,
      [next.task_id]: next,
    }
    syncSelectedJob()
    if (hasActiveJobs()) startJobPolling()
    saveConfig()
    feedback.value = next.status === 'failed'
      ? (next.error_message || 'API 修复任务提交失败。')
      : 'API 修复问题的请求已加入共享队列，完成后会自动回填当前篇目。'
  } catch (error) {
    feedback.value = getApiErrorMessage(error, 'API 修复调用失败。')
  } finally {
    repairing.value = false
  }
}

function downloadOutput() {
  if (!selectedTask.value || !rawJson.value.trim()) return
  const blob = new Blob([rawJson.value], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `ai-annotation-task-${selectedTask.value.id}.json`
  anchor.click()
  URL.revokeObjectURL(url)
  feedback.value = '标注 JSON 已下载。'
}

async function importToDraft() {
  if (!selectedTask.value || !result.value?.document || !canImport.value) return
  if (!window.confirm('将当前 AI 结果覆盖到你的独立盲标草稿中，继续吗？')) return
  importing.value = true
  feedback.value = ''
  try {
    const file = new File(
      [JSON.stringify(result.value.document, null, 2)],
      `ai-annotation-task-${selectedTask.value.id}.json`,
      { type: 'application/json' },
    )
    const detail = await importExtractionDraft(
      selectedTask.value.id,
      selectedTask.value.revision ?? 0,
      file,
      true,
      selectedJob.value?.saved_result_source_job_id
        ?? (selectedJob.value?.status === 'success' ? selectedJob.value.id : undefined),
    )
    const index = tasks.value.findIndex((task) => task.id === selectedTask.value?.id)
    if (index >= 0) {
      tasks.value[index] = {
        ...tasks.value[index],
        revision: detail.submission.revision,
        submission_state: detail.submission.state,
      }
    }
    feedback.value = 'AI 结果已导入独立盲标草稿；超出正文范围的证据片段已忽略，其余约束错误保留，等待人工修改。'
  } catch (error) {
    feedback.value = getApiErrorMessage(error, 'AI 结果导入盲标草稿失败。')
  } finally {
    importing.value = false
  }
}

onMounted(() => {
  loadConfig()
  void loadTasks()
})

onBeforeUnmount(() => {
  saveCurrentDraft()
  stopJobPolling()
})
</script>

<template>
  <AppLayout variant="workspace">
    <div class="ai-workbench">
      <header class="ai-workbench__topbar">
        <div class="ai-workbench__identity">
          <span class="eyebrow">A · AI LAB</span>
          <div>
            <h2>AI 标注工作台</h2>
            <p>从独立盲标任务队列选择篇目，生成可校验、可导入的标注文件。</p>
          </div>
        </div>
        <nav class="mode-switch" aria-label="抽取标注模式">
          <RouterLink to="/annotations/extraction">独立盲标</RouterLink>
          <span>AI 标注</span>
          <RouterLink v-if="authStore.isAdmin" to="/annotations/extraction/ai/metrics">统计</RouterLink>
          <RouterLink to="/annotations/extraction/adjudication">复核裁定</RouterLink>
        </nav>
        <div class="ai-workbench__actions">
          <small v-if="configSaved" class="saved-note">配置已记住（不含 API Key）</small>
          <button type="button" class="button button--quiet" :disabled="tasksLoading" @click="loadTasks">
            {{ tasksLoading ? '刷新中…' : '刷新任务' }}
          </button>
        </div>
      </header>

      <div class="ai-workbench__body">
        <aside class="task-rail">
          <header class="task-rail__heading">
            <div>
              <span>任务篇目</span>
              <strong>{{ tasks.length }}</strong>
            </div>
            <small>与独立盲标共用</small>
          </header>
          <label class="task-search">
            <span>筛选篇目</span>
            <input v-model="searchQuery" type="search" placeholder="标题 / 任务 ID" />
          </label>
          <div class="task-list">
            <p v-if="tasksLoading && !tasks.length" class="empty-note">正在读取任务队列…</p>
            <p v-else-if="!filteredTasks.length" class="empty-note">暂无匹配篇目</p>
            <button
              v-for="(task, index) in filteredTasks"
              :key="task.id"
              type="button"
              class="task-item"
              :class="{ active: selectedTaskId === task.id }"
              @click="openTask(task)"
            >
              <span class="task-item__index">{{ String(index + 1).padStart(2, '0') }}</span>
              <span class="task-item__body">
                <strong>{{ task.passage_title }}</strong>
                <small>任务 #{{ task.id }} · {{ statusLabel(task.status) }}</small>
                <em>{{ submissionLabel(task) }}</em>
                <em
                  class="task-item__ai-status"
                  :data-status="jobsByTaskId[task.id]?.status ?? 'idle'"
                >
                  AI · {{ aiJobStatusLabel(jobsByTaskId[task.id]) }}
                </em>
              </span>
            </button>
          </div>
          <footer class="task-rail__footer">
            <span>AI 生成不会自动领取盲标槽位。</span>
            <RouterLink to="/annotations/extraction">去领取 / 修订</RouterLink>
          </footer>
        </aside>

        <main class="source-panel">
          <header class="source-panel__header">
            <div>
              <span class="panel-kicker">SOURCE TEXT</span>
              <h3>{{ taskContext?.passage.title ?? '选择一个篇目' }}</h3>
            </div>
            <div v-if="taskContext" class="source-panel__stats">
              <span>正文 {{ contextLength.toLocaleString() }} 字</span>
              <span>规范 {{ taskContext.spec_version }}</span>
              <span>{{ sourceTypeLabel(taskContext.passage.source_type) }}</span>
            </div>
          </header>

          <div v-if="contextLoading" class="source-empty">
            <span>载入中</span>
            <p>正在读取当前篇目的正文与版本摘要。</p>
          </div>
          <div v-else-if="!taskContext" class="source-empty">
            <span>先选篇目</span>
            <p>左侧任务列表与独立盲标页面保持一致。</p>
          </div>
          <div v-else class="source-scroll">
            <div class="source-meta">
              <span>任务 #{{ taskContext.task_id }}</span>
              <span>正文摘要 {{ digestPreview(taskContext.context_sha256) }}</span>
              <span>任务状态 {{ statusLabel(taskContext.status) }}</span>
            </div>
            <article class="source-paper">
              <p>{{ taskContext.passage.context }}</p>
            </article>
            <p class="source-hint">
              模型必须以这份正文计算 evidence 的字符位置；正文摘要会写入导出文件，防止后续导入错篇。
            </p>
          </div>
        </main>

        <aside class="inspector">
          <div class="inspector__scroll">
            <section class="inspector-section inspector-section--config">
              <header>
                <span>MODEL CONFIG</span>
                <h3>调用配置</h3>
                <p>仅在本次调用中使用 API Key；其余配置会保存在当前浏览器。</p>
              </header>
              <label class="field">
                <span>Provider</span>
                <select v-model="config.provider">
                  <option value="">跟随服务端默认配置</option>
                  <option value="mock">Mock（离线格式演示）</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="openai_compatible">OpenAI 兼容接口</option>
                </select>
              </label>
              <label class="field">
                <span>Base URL</span>
                <input v-model="config.base_url" type="url" placeholder="https://api.deepseek.com" />
              </label>
              <label class="field">
                <span>模型名称</span>
                <input v-model="config.model" type="text" placeholder="deepseek-chat" />
              </label>
              <label class="field">
                <span>API Key</span>
                <input v-model="config.api_key" type="password" placeholder="留空则使用服务端配置" autocomplete="off" />
              </label>
              <div class="field-row">
                <label class="field">
                  <span>Temperature（可选）</span>
                  <input v-model.number="config.temperature" type="number" min="0" max="2" step="0.1" placeholder="留空则不发送" />
                </label>
              </div>
              <label class="field">
                <span>额外标注要求（可选）</span>
                <textarea v-model="config.extra_instruction" rows="3" placeholder="例如：重点关注人物亲属关系与任职事件。" />
              </label>
              <section class="prompt-viewer">
                <button class="prompt-viewer__toggle" type="button" @click="togglePrompt">
                  <span>
                    <strong>查看本次实际提示词</strong>
                    <small>按照 2026-08-25 分片规范生成，默认使用完整详细规则</small>
                  </span>
                  <b>{{ promptOpen ? '收起' : '展开' }}</b>
                </button>
                <div v-if="promptOpen" class="prompt-viewer__body">
                  <div class="prompt-viewer__meta">
                    <span>{{ promptPreview?.prompt_version ?? '等待加载' }}</span>
                    <button class="button" type="button" :disabled="promptLoading || !taskContext" @click="loadPrompt">
                      {{ promptLoading ? '恢复中…' : '恢复规范提示词' }}
                    </button>
                  </div>
                  <p v-if="promptLoading" class="prompt-viewer__loading">正在组装当前篇目的提示词…</p>
                  <template v-else-if="promptPreview">
                    <p class="prompt-viewer__notice">以下内容允许临时编辑，仅对当前页面后续调用生效，不会写入浏览器配置。</p>
                    <label class="prompt-block">
                      <span>System prompt</span>
                      <textarea v-model="promptPreview.system_prompt" rows="9" spellcheck="false" />
                    </label>
                    <label class="prompt-block">
                      <span>User payload</span>
                      <textarea v-model="promptPreview.user_prompt" rows="14" spellcheck="false" />
                    </label>
                  </template>
                  <p v-else class="prompt-viewer__loading">请先选择篇目。</p>
                </div>
              </section>
              <button class="button button--primary button--wide" type="button" :disabled="!canGenerate" @click="generate">
                {{ generating
                  ? '提交中…'
                  : selectedJob?.status === 'queued'
                    ? '排队中…'
                    : selectedJob?.status === 'running'
                      ? '标注中…'
                      : '调用大模型生成标注' }}
              </button>
              <p class="config-footnote">生成不会修改人工标注，也不会自动提交或领取任务。</p>
            </section>

            <section class="inspector-section inspector-section--result">
              <header class="result-heading">
                <div>
                  <span>ANNOTATION OUTPUT</span>
                  <h3>标注结果</h3>
                </div>
                <b class="status-pill" :data-status="result?.validation_status ?? 'idle'">
                  {{ resultStatusLabel(result?.validation_status) }}
                </b>
              </header>
              <p class="result-hint">{{ resultStatusHint(result?.validation_status) }}</p>

              <div
                v-if="selectedJob"
                class="job-status"
                :data-status="selectedJob.status"
              >
                <div class="job-status__heading">
                  <strong>{{ aiJobStatusLabel(selectedJob) }}</strong>
                  <span>{{ aiJobOperationLabel(selectedJob) }}任务 #{{ selectedJob.id }}</span>
                </div>
                <p>{{ aiJobStatusHint(selectedJob) }}</p>
              </div>

              <div v-if="result" class="result-summary">
                <span v-if="result.provider">{{ result.provider }} · {{ result.model || '默认模型' }}</span>
                <span v-if="result.elapsed_ms">耗时 {{ result.elapsed_ms }} ms</span>
                <span v-if="result.fragment_count">分片 {{ result.fragment_count }}</span>
                <span v-if="result.token_usage?.total_tokens">
                  Token {{ result.token_usage.total_tokens.toLocaleString() }}
                  （输入 {{ result.token_usage.prompt_tokens.toLocaleString() }} / 输出 {{ result.token_usage.completion_tokens.toLocaleString() }}）
                </span>
                <span v-if="result.truncated_fragments?.length" class="result-summary__warning">
                  曾重试截断分片 {{ result.truncated_fragments?.length ?? 0 }} 个
                </span>
                <span v-if="result.fragment_warnings?.length" class="result-summary__warning">
                  {{ result.fragment_warnings?.length ?? 0 }} 个分片待人工复核
                </span>
                <span v-if="result.document">人物 {{ result.document.label.persons.length }} · 关系 {{ result.document.label.person_relations.length }}</span>
              </div>

              <div v-if="result?.validation_issues.length" class="issue-list">
                <div class="issue-list__heading">
                  <strong>需要处理的问题</strong>
                  <span>{{ result.validation_issues.length }} 条</span>
                </div>
                <ul>
                  <li v-for="issue in result.validation_issues.slice(0, 12)" :key="`${issue.path}-${issue.code}`">
                    <code>{{ issue.path }}</code>
                    <span>{{ issue.message }}</span>
                  </li>
                </ul>
                <small v-if="result.validation_issues.length > 12">仅显示前 12 条，导出前请完整检查。</small>
              </div>

              <label class="field output-field">
                <span>可编辑 JSON</span>
                <textarea
                  v-model="rawJson"
                  class="json-editor"
                  spellcheck="false"
                  rows="18"
                  placeholder="生成结果会显示在这里；也可以粘贴外部 JSON 后重新校验。"
                  @input="saveCurrentDraft"
                />
              </label>
              <div class="result-actions">
                <button
                  v-if="result && result.validation_status !== 'valid'"
                  class="button button--repair"
                  type="button"
                  :disabled="!canRepair"
                  @click="repairOutput"
                >
                  {{ repairing
                    ? '提交 API 修复中…'
                    : selectedJob?.operation === 'repair' && selectedJobBusy
                      ? 'API 修复排队中…'
                      : '调用 API 修复问题' }}
                </button>
                <button class="button" type="button" :disabled="!rawJson.trim() || validating || repairing" @click="validateOutput">
                  {{ validating ? '校验中…' : '重新校验 JSON' }}
                </button>
                <button class="button" type="button" :disabled="!rawJson.trim()" @click="downloadOutput">下载 JSON</button>
                <button class="button button--primary" type="button" :disabled="!canImport" @click="importToDraft">
                  {{ importing ? '导入中…' : '一键导入盲标草稿' }}
                </button>
              </div>
              <p v-if="result && result.validation_status !== 'valid'" class="repair-note">
                可调用 API 修复当前问题；修复任务不会修改人工标注，约束错误仍可保留到人工标注页面继续修改。
              </p>
              <p v-if="selectedTask && !canImport" class="import-note">
                {{ result?.validation_status === 'invalid_format' && !result.document
                  ? '当前输出无法解析为 JSON，修复或整理 JSON 后才能导入。'
                  : selectedTask.submission_state === 'submitted'
                  ? '当前篇目的盲标已提交，不能覆盖；可下载后另行处理。'
                  : '要一键导入，请先在独立盲标页面领取该篇目的草稿槽位。' }}
              </p>
            </section>
          </div>
          <footer
            class="inspector__footer"
            :data-error="selectedJob?.status === 'failed' || (Boolean(feedback) && (!result || result.validation_status !== 'valid'))"
          >
            <span v-if="feedback">{{ feedback }}</span>
            <span v-else>输出会携带 task_id、规范版本和正文摘要，便于后续批量导入。</span>
          </footer>
        </aside>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.ai-workbench {
  --ink: #31456f;
  --muted: #7183a8;
  --line: #e4ebf7;
  --blue: #2f6fed;
  --blue-dark: #245bc7;
  --paper: #ffffff;
  min-height: 100%;
  background: #f8fbff;
  color: #44536f;
}

.ai-workbench__topbar {
  min-height: 82px;
  display: grid;
  grid-template-columns: minmax(260px, 1fr) auto auto;
  align-items: center;
  gap: 24px;
  padding: 14px 24px;
  border-bottom: 1px solid #e8eef8;
  background: #fff;
  box-shadow: 0 8px 24px rgba(115, 137, 177, 0.08);
}

.ai-workbench__identity,
.ai-workbench__actions,
.mode-switch,
.result-heading,
.source-panel__stats,
.source-meta,
.result-summary,
.result-actions {
  display: flex;
  align-items: center;
}

.ai-workbench__identity { min-width: 0; gap: 12px; }
.eyebrow { padding: 4px 8px; border-radius: 8px; background: #e6efff; color: var(--blue); font-size: 9px; font-weight: 700; letter-spacing: .04em; white-space: nowrap; }
.ai-workbench__identity h2 { margin: 0; color: var(--ink); font-size: 20px; }
.ai-workbench__identity p { margin: 4px 0 0; color: var(--muted); font-size: 11px; white-space: nowrap; }
.mode-switch { border: 1px solid #dce6f5; border-radius: 10px; overflow: hidden; white-space: nowrap; }
.mode-switch a, .mode-switch span { padding: 7px 11px; color: #687a9e; font-size: 10px; }
.mode-switch a:hover { background: #f0f6ff; color: var(--blue); }
.mode-switch span { background: var(--blue); color: #fff; }
.ai-workbench__actions { justify-content: flex-end; gap: 10px; }
.saved-note { color: #258660; font-size: 10px; white-space: nowrap; }

.button { border: 1px solid #dce6f5; border-radius: 10px; padding: 8px 11px; background: #fff; color: #506287; font-size: 11px; cursor: pointer; }
.button:hover:not(:disabled) { border-color: #8fb3ff; background: #f0f6ff; color: var(--blue); }
.button--primary { border-color: var(--blue); background: var(--blue); color: #fff; }
.button--primary:hover:not(:disabled) { border-color: var(--blue-dark); background: var(--blue-dark); color: #fff; }
.button--repair { border-color: #d5a05d; background: #fffaf0; color: #a2701f; }
.button--repair:hover:not(:disabled) { border-color: #b98235; background: #fff3dc; color: #8b641c; }
.button--wide { width: 100%; padding: 10px; font-weight: 650; }
.button:disabled { cursor: not-allowed; opacity: .48; }

.ai-workbench__body { height: calc(100vh - 82px); display: grid; grid-template-columns: 248px minmax(400px, 1fr) 430px; gap: 16px; padding: 16px; }
.task-rail, .source-panel, .inspector { min-width: 0; border-radius: 18px; background: var(--paper); box-shadow: 0 14px 36px rgba(115, 137, 177, .12); overflow: hidden; }
.task-rail { display: flex; min-height: 0; flex-direction: column; }
.task-rail__heading { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; padding: 18px 16px 12px; }
.task-rail__heading div { display: flex; align-items: baseline; gap: 8px; }
.task-rail__heading span { color: var(--ink); font-size: 13px; font-weight: 700; }
.task-rail__heading strong { color: var(--blue); font: 600 20px ui-monospace, monospace; }
.task-rail__heading small { color: var(--muted); font-size: 9px; }
.task-search { display: grid; gap: 5px; padding: 0 12px 12px; border-bottom: 1px solid #eef2f8; }
.task-search span, .field > span { color: #506287; font-size: 10px; font-weight: 650; }
.task-search input, .field input, .field select, .field textarea { width: 100%; border: 1px solid #dce6f5; border-radius: 10px; outline: none; background: #fff; color: #44536f; font-size: 11px; }
.task-search input, .field input, .field select { padding: 8px 9px; }
.task-search input:focus, .field input:focus, .field select:focus, .field textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(47, 111, 237, .1); }
.task-list { min-height: 0; flex: 1; overflow-y: auto; padding: 8px; }
.task-item { width: 100%; display: grid; grid-template-columns: 23px 1fr; gap: 7px; margin: 4px 0; border: 1px solid #e4ebf7; border-radius: 12px; padding: 10px 9px; background: #fafcff; color: #44536f; text-align: left; cursor: pointer; }
.task-item:hover { border-color: #8fb3ff; background: #f0f6ff; }
.task-item.active { border-color: var(--blue); background: #eef5ff; }
.task-item__index { color: #8a97b3; font: 10px ui-monospace, monospace; }
.task-item__body { min-width: 0; display: grid; gap: 3px; }
.task-item__body strong { overflow: hidden; color: var(--ink); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.task-item__body small, .task-item__body em { overflow: hidden; color: var(--muted); font-size: 9px; font-style: normal; text-overflow: ellipsis; white-space: nowrap; }
.task-item__body em { color: #5c8c79; }
.task-item__body .task-item__ai-status { color: #7183a8; }
.task-item__body .task-item__ai-status[data-status='queued'],
.task-item__body .task-item__ai-status[data-status='running'] { color: #a2701f; }
.task-item__body .task-item__ai-status[data-status='success'] { color: #258660; }
.task-item__body .task-item__ai-status[data-status='failed'] { color: #c04f56; }
.empty-note { padding: 20px 10px; color: var(--muted); text-align: center; font-size: 10px; line-height: 1.6; }
.task-rail__footer { display: grid; gap: 6px; padding: 12px; border-top: 1px solid #eef2f8; color: var(--muted); font-size: 9px; line-height: 1.5; }
.task-rail__footer a { color: var(--blue); }

.source-panel { display: flex; min-height: 0; flex-direction: column; }
.source-panel__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding: 20px 22px 16px; border-bottom: 1px solid #e9eef8; background: #fafcff; }
.panel-kicker, .inspector-section header > span { color: var(--blue); font: 9px ui-monospace, monospace; letter-spacing: .11em; }
.source-panel h3 { margin: 5px 0 0; color: var(--ink); font-size: 18px; }
.source-panel__stats { flex-wrap: wrap; justify-content: flex-end; gap: 5px 12px; color: var(--muted); font-size: 9px; text-align: right; }
.source-scroll { min-height: 0; flex: 1; overflow-y: auto; padding: 18px 22px 28px; }
.source-meta { flex-wrap: wrap; gap: 6px 14px; padding-bottom: 12px; color: #7183a8; font: 10px ui-monospace, monospace; }
.source-paper { min-height: 60%; padding: 28px 30px; border: 1px solid #e5ecf7; border-radius: 14px; background: #fff; box-shadow: 0 6px 18px rgba(115, 137, 177, .06); }
.source-paper p { margin: 0; color: #3f4e6a; font-family: 'Noto Serif SC', 'Songti SC', serif; font-size: 17px; line-height: 2.15; white-space: pre-wrap; word-break: break-all; }
.source-hint { margin: 14px 2px 0; color: var(--muted); font-size: 10px; line-height: 1.6; }
.source-empty { flex: 1; display: grid; place-content: center; gap: 6px; padding: 30px; text-align: center; }
.source-empty span { justify-self: center; padding: 5px 9px; border: 1px solid #b8cdf6; border-radius: 8px; background: #f5f8ff; color: var(--blue); font-size: 10px; }
.source-empty p { margin: 0; color: var(--muted); font-size: 11px; }

.inspector { display: flex; min-height: 0; flex-direction: column; }
.inspector__scroll { min-height: 0; flex: 1; overflow-y: auto; }
.inspector-section { display: grid; gap: 12px; padding: 20px; }
.inspector-section + .inspector-section { border-top: 1px solid #e9eef8; }
.inspector-section header { padding-bottom: 12px; border-bottom: 1px solid #e9eef8; }
.inspector-section header h3 { margin: 5px 0 5px; color: var(--ink); font-size: 16px; }
.inspector-section header p, .config-footnote, .import-note, .result-hint { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.6; }
.field { display: grid; gap: 5px; min-width: 0; }
.field textarea { resize: vertical; padding: 8px 9px; line-height: 1.5; }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; }
.prompt-viewer { display: grid; gap: 8px; }
.prompt-viewer__toggle { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; border: 1px solid #dce6f5; border-radius: 10px; padding: 10px; background: #f8fbff; color: #506287; text-align: left; cursor: pointer; }
.prompt-viewer__toggle:hover { border-color: #8fb3ff; background: #f0f6ff; }
.prompt-viewer__toggle span { display: grid; gap: 3px; }
.prompt-viewer__toggle strong { color: var(--ink); font-size: 10px; }
.prompt-viewer__toggle small { color: var(--muted); font-size: 9px; }
.prompt-viewer__toggle b { flex: 0 0 auto; color: var(--blue); font-size: 9px; font-weight: 650; }
.prompt-viewer__body { display: grid; gap: 9px; padding: 10px; border: 1px solid #e4ebf7; border-radius: 10px; background: #fbfdff; }
.prompt-viewer__meta { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--muted); font: 9px ui-monospace, monospace; }
.prompt-viewer__meta .button { padding: 5px 7px; font-size: 9px; }
.prompt-viewer__loading { margin: 0; color: var(--muted); font-size: 9px; line-height: 1.5; }
.prompt-viewer__notice { margin: 0; color: #8b7b62; font-size: 9px; line-height: 1.5; }
.prompt-block { display: grid; gap: 5px; min-width: 0; }
.prompt-block > span { color: #506287; font-size: 9px; font-weight: 650; }
.prompt-block textarea { max-height: 300px; min-height: 120px; margin: 0; resize: vertical; overflow: auto; border: 1px solid #e4ebf7; border-radius: 8px; padding: 8px; outline: none; background: #fff; color: #536789; font: 9px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; word-break: break-word; }
.prompt-block textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(47, 111, 237, .1); }
.result-heading { justify-content: space-between; gap: 10px; }
.result-heading header { border: 0; padding: 0; }
.status-pill { flex: 0 0 auto; border-radius: 999px; padding: 5px 8px; background: #edf1f7; color: #7183a8; font-size: 9px; font-weight: 600; }
.status-pill[data-status='valid'] { background: #e8f7ef; color: #258660; }
.status-pill[data-status='invalid_rules'] { background: #fff5df; color: #a2701f; }
.status-pill[data-status='invalid_format'] { background: #ffebeb; color: #c04f56; }
.job-status { display: grid; gap: 5px; padding: 10px; border: 1px solid #dce6f5; border-radius: 10px; background: #f8fbff; }
.job-status__heading { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.job-status__heading strong { color: var(--blue); font-size: 10px; }
.job-status__heading span { color: var(--muted); font: 9px ui-monospace, monospace; }
.job-status p { margin: 0; color: var(--muted); font-size: 9px; line-height: 1.55; }
.job-status[data-status='queued'], .job-status[data-status='running'] { border-color: #f0dfb6; background: #fffaf0; }
.job-status[data-status='queued'] .job-status__heading strong, .job-status[data-status='running'] .job-status__heading strong { color: #a2701f; }
.job-status[data-status='success'] { border-color: #c9e9d9; background: #f5fcf8; }
.job-status[data-status='success'] .job-status__heading strong { color: #258660; }
.job-status[data-status='failed'] { border-color: #f2d9d9; background: #fff8f8; }
.job-status[data-status='failed'] .job-status__heading strong, .job-status[data-status='failed'] p { color: #b44e55; }
.result-summary { flex-wrap: wrap; gap: 5px 10px; color: #7183a8; font: 9px ui-monospace, monospace; }
.result-summary__warning { color: #b06a00; }
.issue-list { display: grid; gap: 7px; padding: 10px; border: 1px solid #f2d9d9; border-radius: 10px; background: #fff8f8; }
.issue-list__heading { display: flex; justify-content: space-between; color: #b44e55; font-size: 10px; }
.issue-list__heading span { color: #c9898d; }
.issue-list ul { display: grid; gap: 6px; max-height: 190px; margin: 0; padding: 0; overflow: auto; list-style: none; }
.issue-list li { display: grid; gap: 2px; color: #715d64; font-size: 9px; line-height: 1.45; }
.issue-list code { color: #b44e55; font-size: 9px; }
.issue-list > small { color: #a78387; font-size: 9px; }
.json-editor { min-height: 280px; color: #344e7d !important; font: 10px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace !important; white-space: pre; tab-size: 2; }
.result-actions { flex-wrap: wrap; gap: 7px; }
.result-actions .button { flex: 1 1 auto; }
.repair-note { margin: -3px 0 0; color: #8b7b62; font-size: 9px; line-height: 1.55; }
.import-note { color: #8b7b62; }
.inspector__footer { min-height: 39px; display: flex; align-items: center; padding: 9px 14px; border-top: 1px solid #e4ebf7; background: #f5f8ff; color: var(--muted); font-size: 9px; line-height: 1.4; }
.inspector__footer[data-error='true'] { background: #fff8f8; color: #b44e55; }

@media (max-width: 1320px) {
  .ai-workbench__topbar { grid-template-columns: minmax(240px, 1fr) auto; }
  .mode-switch { display: none; }
  .ai-workbench__body { grid-template-columns: 220px minmax(360px, 1fr) 380px; }
  .ai-workbench__identity p { white-space: normal; }
}

@media (max-width: 1050px) {
  .ai-workbench { min-height: 100vh; }
  .ai-workbench__topbar { position: sticky; top: 0; z-index: 5; }
  .ai-workbench__body { height: auto; grid-template-columns: 190px minmax(360px, 1fr); }
  .task-rail, .source-panel { min-height: calc(100vh - 114px); }
  .inspector { grid-column: 1 / -1; min-height: 700px; }
  .inspector__scroll { overflow: visible; }
}

@media (max-width: 760px) {
  .ai-workbench__topbar { grid-template-columns: 1fr; gap: 8px; padding: 12px 16px; }
  .ai-workbench__actions { justify-content: flex-start; }
  .ai-workbench__body { grid-template-columns: 1fr; padding: 10px; }
  .task-rail { min-height: 0; max-height: 310px; }
  .source-panel { min-height: 70vh; }
  .source-panel__header { display: block; }
  .source-panel__stats { justify-content: flex-start; margin-top: 10px; text-align: left; }
  .source-paper { padding: 20px; }
  .source-paper p { font-size: 15px; }
  .field-row { grid-template-columns: 1fr; }
}
</style>

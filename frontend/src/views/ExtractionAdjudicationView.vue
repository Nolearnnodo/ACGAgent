<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import {
  type AdjudicationDecision,
  type AdjudicationDifference,
  type AdjudicationResolution,
  type EvidenceSpan,
  type ExtractionAdjudicationDetail,
  type ExtractionAdjudicationSummary,
  type ExtractionAnnotationLabel,
  downloadExtractionGold,
  fetchExtractionAdjudication,
  listExtractionAdjudications,
  lockExtractionGold,
  saveExtractionAdjudicationDraft,
} from '../api/extractionAnnotations'
import { getApiErrorMessage } from '../api/client'
import AnnotationTextCanvas from '../components/annotation/AnnotationTextCanvas.vue'
import AppLayout from '../layouts/AppLayout.vue'
import { collectHighlights } from '../utils/extractionAnnotation'

type CandidateView = 'a' | 'b' | 'gold'
type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'

const tasks = ref<ExtractionAdjudicationSummary[]>([])
const detail = ref<ExtractionAdjudicationDetail | null>(null)
const loading = ref(false)
const openingTaskId = ref<number | null>(null)
const activeView = ref<CandidateView>('a')
const activeEvidenceId = ref<string | null>(null)
const resolutions = ref<Record<string, AdjudicationResolution>>({})
const manualTexts = ref<Record<string, string>>({})
const changeReason = ref('')
const saveState = ref<SaveState>('idle')
const feedback = ref('')
const locking = ref(false)
const exporting = ref(false)
const differenceFilter = ref('all')
const textCanvas = ref<InstanceType<typeof AnnotationTextCanvas> | null>(null)

let saveTimer: number | null = null

const activeLabel = computed<ExtractionAnnotationLabel | null>(() => {
  if (!detail.value) return null
  if (activeView.value === 'gold') return detail.value.draft.gold_label
  const slot = activeView.value === 'a' ? 1 : 2
  return detail.value.submissions.find((item) => item.slot_no === slot)?.label ?? null
})
const highlights = computed(() => collectHighlights(activeLabel.value))
const resolvedCount = computed(() => Object.keys(resolutions.value).length)
const unresolvedCount = computed(() => Math.max((detail.value?.differences.length ?? 0) - resolvedCount.value, 0))
const filteredDifferences = computed(() => {
  const differences = detail.value?.differences ?? []
  if (differenceFilter.value === 'all') return differences
  if (differenceFilter.value === 'unresolved') {
    return differences.filter((item) => !resolutions.value[item.id])
  }
  return differences.filter((item) => item.category === differenceFilter.value)
})
const canLock = computed(() => Boolean(
  detail.value
  && unresolvedCount.value === 0
  && !locking.value
  && saveState.value !== 'saving'
  && (!detail.value.latest_gold || changeReason.value.trim()),
))

function statusLabel(status: string) {
  return status === 'completed' ? '已锁定金标' : '待裁定'
}

function saveStateLabel() {
  return {
    idle: '尚未修改',
    dirty: '有未保存裁定',
    saving: '保存中…',
    saved: '裁定草稿已保存',
    error: '保存失败',
  }[saveState.value]
}

function stringifyValue(value: unknown, compact = false) {
  if (value === null || value === undefined) return '（无）'
  if (typeof value === 'string') return value || '（空字符串）'
  const text = JSON.stringify(value, null, compact ? 0 : 2)
  if (!text) return String(value)
  return compact && text.length > 140 ? `${text.slice(0, 138)}…` : text
}

function decisionLabel(decision?: AdjudicationDecision) {
  if (decision === 'a') return '采用 A'
  if (decision === 'b') return '采用 B'
  if (decision === 'manual') return '手工裁定'
  return '未裁定'
}

function hydrate(next: ExtractionAdjudicationDetail) {
  detail.value = next
  resolutions.value = Object.fromEntries(
    next.draft.resolutions.map((item) => [item.difference_id, structuredClone(item)]),
  )
  manualTexts.value = Object.fromEntries(
    next.draft.resolutions
      .filter((item) => item.decision === 'manual')
      .map((item) => [item.difference_id, JSON.stringify(item.manual_value, null, 2)]),
  )
  changeReason.value = next.draft.change_reason
  activeView.value = next.latest_gold ? 'gold' : 'a'
  saveState.value = 'saved'
}

async function loadTasks() {
  loading.value = true
  try {
    tasks.value = await listExtractionAdjudications()
    if (!detail.value && tasks.value.length) await openTask(tasks.value[0].task_id)
  } catch (error) {
    feedback.value = getApiErrorMessage(error, '裁定队列加载失败。')
  } finally {
    loading.value = false
  }
}

async function openTask(taskId: number) {
  if (saveState.value === 'dirty' && !window.confirm('当前裁定草稿尚未保存，仍要切换任务吗？')) return
  openingTaskId.value = taskId
  feedback.value = ''
  try {
    hydrate(await fetchExtractionAdjudication(taskId))
  } catch (error) {
    feedback.value = getApiErrorMessage(error, '裁定详情加载失败。')
  } finally {
    openingTaskId.value = null
  }
}

function clearSaveTimer() {
  if (saveTimer !== null) window.clearTimeout(saveTimer)
  saveTimer = null
}

function scheduleSave() {
  saveState.value = 'dirty'
  clearSaveTimer()
  saveTimer = window.setTimeout(() => void saveDraft(true), 700)
}

function setResolution(difference: AdjudicationDifference, decision: AdjudicationDecision) {
  const existing = resolutions.value[difference.id]
  const manualValue = decision === 'manual'
    ? existing?.manual_value ?? difference.value_a
    : null
  resolutions.value[difference.id] = {
    difference_id: difference.id,
    decision,
    manual_value: manualValue,
    note: existing?.note ?? '',
  }
  if (decision === 'manual' && !(difference.id in manualTexts.value)) {
    manualTexts.value[difference.id] = JSON.stringify(manualValue, null, 2)
  }
  scheduleSave()
}

function setAllResolutions(decision: 'a' | 'b') {
  for (const difference of detail.value?.differences ?? []) setResolution(difference, decision)
  scheduleSave()
}

function updateManualValue(differenceId: string) {
  const resolution = resolutions.value[differenceId]
  if (!resolution) return
  try {
    resolution.manual_value = JSON.parse(manualTexts.value[differenceId] ?? 'null')
    feedback.value = ''
    scheduleSave()
  } catch {
    saveState.value = 'error'
    feedback.value = '手工裁定值不是合法 JSON，草稿尚未保存。'
  }
}

function currentPayload() {
  for (const [differenceId, resolution] of Object.entries(resolutions.value)) {
    if (resolution.decision !== 'manual') continue
    try {
      resolution.manual_value = JSON.parse(manualTexts.value[differenceId] ?? 'null')
    } catch {
      throw new Error('手工裁定值不是合法 JSON。')
    }
  }
  return {
    revision: detail.value?.draft.revision ?? 0,
    resolutions: Object.values(resolutions.value),
    change_reason: changeReason.value,
  }
}

async function saveDraft(silent = false) {
  if (!detail.value || saveState.value === 'saving') return false
  clearSaveTimer()
  saveState.value = 'saving'
  try {
    const next = await saveExtractionAdjudicationDraft(detail.value.task_id, currentPayload())
    hydrate(next)
    if (!silent) feedback.value = '裁定草稿已保存。'
    return true
  } catch (error) {
    saveState.value = 'error'
    feedback.value = getApiErrorMessage(error, '裁定草稿保存失败。')
    return false
  }
}

async function handleLock() {
  if (!detail.value || !canLock.value) return
  if (!window.confirm('锁定后将生成不可变金标版本。确定继续吗？')) return
  if (saveState.value === 'dirty' && !(await saveDraft(true))) return
  locking.value = true
  feedback.value = ''
  try {
    const next = await lockExtractionGold(detail.value.task_id, currentPayload())
    hydrate(next)
    feedback.value = `金标 v${next.latest_gold?.version} 已锁定，可导出 YAML。`
    await loadTasks()
  } catch (error) {
    feedback.value = getApiErrorMessage(error, '金标锁定失败。')
  } finally {
    locking.value = false
  }
}

async function handleExport() {
  if (!detail.value?.latest_gold) return
  exporting.value = true
  try {
    const { blob, filename } = await downloadExtractionGold(
      detail.value.task_id,
      detail.value.latest_gold.version,
    )
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    feedback.value = getApiErrorMessage(error, '金标导出失败。')
  } finally {
    exporting.value = false
  }
}

function findEvidence(value: unknown): EvidenceSpan | null {
  if (!value || typeof value !== 'object') return null
  if (
    'id' in value && 'quote' in value && 'start' in value && 'end' in value
    && typeof value.id === 'string' && typeof value.quote === 'string'
  ) return value as EvidenceSpan
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findEvidence(item)
      if (found) return found
    }
  } else {
    for (const item of Object.values(value)) {
      const found = findEvidence(item)
      if (found) return found
    }
  }
  return null
}

async function focusDifference(difference: AdjudicationDifference) {
  const decision = resolutions.value[difference.id]?.decision
  const side: CandidateView = decision === 'b' ? 'b' : decision === 'manual' ? 'gold' : 'a'
  const evidence = findEvidence(side === 'b' ? difference.value_b : difference.value_a)
  if (!evidence) return
  activeView.value = side
  activeEvidenceId.value = evidence.id
  await nextTick()
  textCanvas.value?.focusEvidence(evidence.id)
}

function markReasonDirty() {
  scheduleSave()
}

onMounted(loadTasks)
onBeforeUnmount(clearSaveTimer)
</script>

<template>
  <AppLayout variant="workspace">
    <div class="adjudication">
      <header class="adjudication__topbar">
        <div class="adjudication__identity">
          <span>A · REVIEW</span>
          <div>
            <h2>抽取复核裁定</h2>
            <p v-if="detail">任务 #{{ detail.task_id }} · {{ detail.passage.title }}</p>
            <p v-else>等待两份独立盲标汇合</p>
          </div>
        </div>
        <nav class="mode-switch" aria-label="标注模式">
          <RouterLink to="/annotations/extraction">独立盲标</RouterLink>
          <span>复核裁定</span>
        </nav>
        <div class="adjudication__actions">
          <small :data-state="saveState">{{ saveStateLabel() }}</small>
          <button type="button" :disabled="!detail || saveState === 'saving'" @click="saveDraft(false)">保存裁定</button>
          <button class="primary" type="button" :disabled="!canLock" @click="handleLock">
            {{ locking ? '锁定中…' : detail?.latest_gold ? '建立新版金标' : '锁定金标' }}
          </button>
          <button type="button" :disabled="!detail?.latest_gold || exporting" @click="handleExport">导出 YAML</button>
        </div>
      </header>

      <div class="adjudication__body">
        <aside class="review-queue">
          <header>
            <div><span>裁定队列</span><strong>{{ tasks.length }}</strong></div>
            <button type="button" title="刷新" @click="loadTasks">↻</button>
          </header>
          <p v-if="loading" class="empty-note">正在读取双盲结果…</p>
          <p v-else-if="!tasks.length" class="empty-note">暂无已完成双盲的任务。</p>
          <button
            v-for="(task, index) in tasks"
            :key="task.task_id"
            class="queue-item"
            :class="{ active: detail?.task_id === task.task_id }"
            type="button"
            :disabled="openingTaskId === task.task_id"
            @click="openTask(task.task_id)"
          >
            <i>{{ String(index + 1).padStart(2, '0') }}</i>
            <strong>{{ task.passage_title }}</strong>
            <span>{{ statusLabel(task.task_status) }}</span>
            <small>{{ task.resolved_count }}/{{ task.difference_count }} 已裁定</small>
          </button>
        </aside>

        <main class="source-review">
          <template v-if="detail">
            <header class="source-review__header">
              <div>
                <span>DOC {{ String(detail.passage.doc_id).padStart(4, '0') }}</span>
                <h3>{{ detail.passage.title }}</h3>
              </div>
              <div class="candidate-tabs">
                <button
                  v-for="item in ([
                    { value: 'a', label: '盲标 A', enabled: true },
                    { value: 'b', label: '盲标 B', enabled: detail.submissions.length > 1 },
                    { value: 'gold', label: '裁定稿', enabled: true },
                  ] as const)"
                  :key="item.value"
                  type="button"
                  :disabled="!item.enabled"
                  :class="{ active: activeView === item.value }"
                  @click="activeView = item.value"
                >{{ item.label }}</button>
              </div>
              <div class="source-review__counts">
                <strong>{{ unresolvedCount }}</strong><span>待裁定</span>
              </div>
            </header>
            <div class="source-review__canvas">
              <AnnotationTextCanvas
                ref="textCanvas"
                :text="detail.passage.context"
                :highlights="highlights"
                :active-evidence-id="activeEvidenceId"
                disabled
                @highlight-activated="activeEvidenceId = $event"
              />
            </div>
            <footer>
              <span>当前查看：{{ activeView === 'a' ? '盲标 A' : activeView === 'b' ? '盲标 B' : '裁定稿' }}</span>
              <span>{{ highlights.length }} 处可回放证据</span>
            </footer>
          </template>
          <div v-else class="source-review__empty">
            <span>校</span><h3>选择一项待裁定任务</h3><p>原文与两份人工标签将在这里汇合，不显示任何 AI 输出。</p>
          </div>
        </main>

        <aside class="difference-ledger">
          <template v-if="detail">
            <header class="ledger-header">
              <div><span>差异账本</span><strong>{{ resolvedCount }}/{{ detail.differences.length }}</strong></div>
              <small v-if="detail.latest_gold">金标 v{{ detail.latest_gold.version }} · 已锁定</small>
              <small v-else>逐项裁定后方可锁定</small>
            </header>

            <div class="bulk-actions">
              <button type="button" @click="setAllResolutions('a')">全部采用 A</button>
              <button type="button" @click="setAllResolutions('b')">全部采用 B</button>
            </div>

            <div class="ledger-filter">
              <button
                v-for="item in ([
                  { value: 'all', label: '全部' }, { value: 'unresolved', label: '未裁定' },
                  { value: 'passage', label: '文献' }, { value: 'person', label: '人物' },
                  { value: 'relation', label: '关系' }, { value: 'review', label: '其他' },
                ] as const)"
                :key="item.value"
                type="button"
                :class="{ active: differenceFilter === item.value }"
                @click="differenceFilter = item.value"
              >{{ item.label }}</button>
            </div>

            <div class="ledger-scroll">
              <article
                v-for="(difference, index) in filteredDifferences"
                :key="difference.id"
                class="difference"
                :data-resolved="Boolean(resolutions[difference.id])"
              >
                <header @click="focusDifference(difference)">
                  <i>{{ String(index + 1).padStart(2, '0') }}</i>
                  <div><strong>{{ difference.label }}</strong><small>{{ difference.path }}</small></div>
                  <span>{{ decisionLabel(resolutions[difference.id]?.decision) }}</span>
                </header>
                <div class="candidate-values">
                  <button
                    type="button"
                    :class="{ chosen: resolutions[difference.id]?.decision === 'a' }"
                    @click="setResolution(difference, 'a')"
                  ><b>A</b><span>{{ stringifyValue(difference.value_a, true) }}</span></button>
                  <button
                    type="button"
                    :class="{ chosen: resolutions[difference.id]?.decision === 'b' }"
                    @click="setResolution(difference, 'b')"
                  ><b>B</b><span>{{ stringifyValue(difference.value_b, true) }}</span></button>
                </div>
                <button class="manual-toggle" type="button" @click="setResolution(difference, 'manual')">手工裁定</button>
                <div v-if="resolutions[difference.id]?.decision === 'manual'" class="manual-editor">
                  <label>最终值（JSON）<textarea v-model="manualTexts[difference.id]" rows="5" @input="updateManualValue(difference.id)" /></label>
                  <label>裁定说明<input v-model.trim="resolutions[difference.id].note" placeholder="说明采用新值的依据" @input="scheduleSave" /></label>
                </div>
              </article>
              <p v-if="!filteredDifferences.length" class="empty-note">
                {{ detail.differences.length ? '当前筛选下没有差异。' : '两份标注语义一致，可直接锁定金标。' }}
              </p>

              <section class="lock-note">
                <label>
                  <span>{{ detail.latest_gold ? '新版修订原因（必填）' : '本次裁定说明' }}</span>
                  <textarea v-model.trim="changeReason" rows="3" placeholder="记录边界取舍、材料问题或新版修订原因" @input="markReasonDirty" />
                </label>
                <p>锁定时后端会重新校验证据位置、字典、人物层级、事件约束与正反关系链。</p>
              </section>
            </div>
            <footer :data-error="saveState === 'error'">{{ feedback || `${unresolvedCount} 项尚待裁定` }}</footer>
          </template>
          <div v-else class="ledger-empty">等待选择任务</div>
        </aside>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.adjudication {
  --ink: #2d2a25;
  --muted: #797268;
  --paper: #fbf8ef;
  --paper-deep: #f1eadc;
  --line: #ded6c8;
  --vermilion: #a34432;
  --teal: #416f77;
  height: 100vh;
  min-width: 0;
  overflow: hidden;
  background: var(--paper);
  color: var(--ink);
}
.adjudication button, .adjudication input, .adjudication textarea { font: inherit; }
.adjudication__topbar { height: 76px; display: grid; grid-template-columns: minmax(290px, 1fr) auto minmax(370px, 1fr); align-items: center; gap: 20px; padding: 0 24px; border-bottom: 1px solid var(--line); background: #f5efe4; box-sizing: border-box; }
.adjudication__identity { display: flex; align-items: center; gap: 14px; min-width: 0; }
.adjudication__identity > span { writing-mode: vertical-rl; color: var(--vermilion); font: 8px/1 ui-monospace, monospace; letter-spacing: .12em; }
.adjudication__identity h2 { margin: 0; font: 600 19px 'Noto Serif SC', serif; }
.adjudication__identity p { margin: 3px 0 0; overflow: hidden; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.mode-switch { display: flex; border-bottom: 1px solid #c9bfae; }
.mode-switch a, .mode-switch span { padding: 7px 14px; color: var(--muted); font-size: 11px; text-decoration: none; }
.mode-switch span { border-bottom: 2px solid var(--vermilion); color: var(--vermilion); font-weight: 650; }
.adjudication__actions { display: flex; justify-content: flex-end; align-items: center; gap: 7px; }
.adjudication__actions small { margin-right: 4px; color: var(--muted); font-size: 9px; }
.adjudication__actions small[data-state='error'] { color: var(--vermilion); }
.adjudication__actions button, .bulk-actions button { border: 1px solid #cfc5b6; padding: 7px 10px; background: transparent; color: var(--ink); cursor: pointer; font-size: 10px; }
.adjudication__actions button.primary { border-color: var(--vermilion); background: var(--vermilion); color: #fffaf0; }
.adjudication__actions button:disabled { opacity: .42; cursor: not-allowed; }
.adjudication__body { height: calc(100vh - 76px); display: grid; grid-template-columns: 178px minmax(430px, 1fr) 410px; min-width: 0; }
.review-queue, .difference-ledger { min-height: 0; background: var(--paper-deep); }
.review-queue { border-right: 1px solid var(--line); overflow-y: auto; }
.review-queue > header { position: sticky; top: 0; z-index: 2; display: flex; justify-content: space-between; align-items: center; padding: 16px 14px 11px; border-bottom: 1px solid var(--line); background: var(--paper-deep); }
.review-queue > header div { display: flex; gap: 7px; font-size: 11px; }
.review-queue > header strong { color: var(--vermilion); }
.review-queue > header button { border: 0; background: transparent; color: var(--muted); cursor: pointer; }
.queue-item { width: 100%; display: grid; grid-template-columns: 24px 1fr; gap: 3px 7px; padding: 12px 11px; border: 0; border-bottom: 1px solid rgba(150, 137, 118, .18); background: transparent; color: var(--ink); text-align: left; cursor: pointer; }
.queue-item:hover { background: #ebe2d3; }
.queue-item.active { background: var(--paper); box-shadow: inset 3px 0 var(--vermilion); }
.queue-item i { grid-row: span 3; color: #9d9385; font: normal 9px ui-monospace, monospace; }
.queue-item strong { overflow: hidden; font: 600 12px 'Noto Serif SC', serif; text-overflow: ellipsis; white-space: nowrap; }
.queue-item span, .queue-item small { color: var(--muted); font-size: 9px; }
.source-review { min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--paper); }
.source-review__header { min-height: 72px; display: grid; grid-template-columns: 1fr auto 74px; align-items: center; gap: 18px; padding: 0 22px; border-bottom: 1px solid var(--line); }
.source-review__header > div:first-child span { color: var(--vermilion); font: 8px ui-monospace, monospace; letter-spacing: .12em; }
.source-review__header h3 { margin: 4px 0 0; font: 600 17px 'Noto Serif SC', serif; }
.candidate-tabs { display: flex; }
.candidate-tabs button { border: 0; border-bottom: 1px solid #cfc5b6; padding: 7px 11px; background: transparent; color: var(--muted); cursor: pointer; font-size: 10px; }
.candidate-tabs button.active { border-bottom: 2px solid var(--teal); color: var(--ink); font-weight: 650; }
.candidate-tabs button:disabled { opacity: .35; }
.source-review__counts { display: flex; align-items: baseline; justify-content: flex-end; gap: 4px; }
.source-review__counts strong { color: var(--vermilion); font: 600 22px 'Noto Serif SC', serif; }
.source-review__counts span { color: var(--muted); font-size: 9px; }
.source-review__canvas { flex: 1; min-height: 0; overflow-y: auto; }
.source-review > footer { display: flex; justify-content: space-between; padding: 8px 18px; border-top: 1px solid var(--line); color: var(--muted); font-size: 9px; }
.source-review__empty { flex: 1; display: grid; place-content: center; justify-items: center; color: var(--muted); text-align: center; }
.source-review__empty > span { width: 56px; height: 56px; display: grid; place-items: center; border: 1px solid #c9bfae; color: var(--vermilion); font: 600 25px 'Noto Serif SC', serif; transform: rotate(-3deg); }
.source-review__empty h3 { margin: 20px 0 6px; color: var(--ink); font: 600 18px 'Noto Serif SC', serif; }
.source-review__empty p { margin: 0; font-size: 11px; }
.difference-ledger { display: flex; flex-direction: column; border-left: 1px solid var(--line); }
.ledger-header { padding: 14px 17px 10px; border-bottom: 1px solid var(--line); }
.ledger-header div { display: flex; justify-content: space-between; font-size: 12px; }
.ledger-header strong { color: var(--vermilion); }
.ledger-header small { display: block; margin-top: 3px; color: var(--muted); font-size: 9px; }
.bulk-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; padding: 9px 12px 0; }
.ledger-filter { display: flex; gap: 4px; padding: 9px 12px; overflow-x: auto; }
.ledger-filter button { flex: 0 0 auto; border: 0; border-bottom: 1px solid #cfc5b6; padding: 5px 6px; background: transparent; color: var(--muted); cursor: pointer; font-size: 9px; }
.ledger-filter button.active { border-bottom-color: var(--vermilion); color: var(--vermilion); }
.ledger-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 0 12px 60px; }
.difference { margin-bottom: 9px; border: 1px solid #d8cfc1; background: var(--paper); }
.difference[data-resolved='true'] { border-left: 3px solid var(--teal); }
.difference > header { display: grid; grid-template-columns: 22px 1fr auto; gap: 7px; align-items: start; padding: 10px; cursor: pointer; }
.difference > header i { color: #9d9385; font: normal 8px ui-monospace, monospace; }
.difference > header strong { display: block; font: 600 11px 'Noto Serif SC', serif; }
.difference > header small { display: block; margin-top: 2px; color: var(--muted); font: 8px ui-monospace, monospace; }
.difference > header span { color: var(--vermilion); font-size: 8px; }
.candidate-values { display: grid; grid-template-columns: 1fr 1fr; border-top: 1px solid #e5ddd1; }
.candidate-values button { min-width: 0; display: grid; grid-template-columns: 18px 1fr; gap: 5px; border: 0; border-right: 1px solid #e5ddd1; padding: 8px; background: transparent; color: var(--ink); text-align: left; cursor: pointer; }
.candidate-values button:last-child { border-right: 0; }
.candidate-values button.chosen { background: #e6efea; }
.candidate-values b { color: var(--vermilion); font: 600 9px ui-monospace, monospace; }
.candidate-values span { overflow: hidden; font-size: 9px; line-height: 1.45; overflow-wrap: anywhere; }
.manual-toggle { margin: 7px 9px 9px; border: 0; border-bottom: 1px solid #bdb2a2; padding: 2px 0; background: transparent; color: var(--muted); cursor: pointer; font-size: 9px; }
.manual-editor { display: grid; gap: 7px; padding: 0 9px 10px; }
.manual-editor label, .lock-note label { display: grid; gap: 4px; color: var(--muted); font-size: 9px; }
.manual-editor textarea, .manual-editor input, .lock-note textarea { width: 100%; box-sizing: border-box; border: 1px solid #cfc5b6; padding: 7px; background: #fffdf7; color: var(--ink); font: 9px/1.5 ui-monospace, monospace; resize: vertical; }
.lock-note { margin-top: 16px; padding: 13px 2px; border-top: 1px solid #cfc5b6; }
.lock-note p { margin: 8px 0 0; color: var(--muted); font-size: 9px; line-height: 1.55; }
.difference-ledger > footer { min-height: 34px; display: flex; align-items: center; padding: 6px 13px; border-top: 1px solid var(--line); color: var(--muted); font-size: 9px; }
.difference-ledger > footer[data-error='true'] { color: var(--vermilion); }
.empty-note, .ledger-empty { padding: 25px 15px; color: var(--muted); font-size: 10px; line-height: 1.65; text-align: center; }
.ledger-empty { margin: auto; }
@media (max-width: 1250px) {
  .adjudication__body { grid-template-columns: 150px minmax(360px, 1fr) 360px; }
  .adjudication__topbar { grid-template-columns: minmax(240px, 1fr) auto minmax(310px, 1fr); }
}
@media (max-width: 980px) {
  .adjudication { height: auto; min-height: 100vh; overflow: visible; }
  .adjudication__topbar { height: auto; grid-template-columns: 1fr; padding: 14px 18px; }
  .adjudication__actions { justify-content: flex-start; flex-wrap: wrap; }
  .adjudication__body { height: auto; grid-template-columns: 1fr; }
  .review-queue { max-height: 210px; border-right: 0; border-bottom: 1px solid var(--line); }
  .source-review { min-height: 70vh; }
  .difference-ledger { min-height: 70vh; border-left: 0; border-top: 1px solid var(--line); }
}

/* 与项目现有审核类子页面统一：浅蓝背景、白色面板、蓝色主操作。 */
.adjudication {
  --ink: #31456f;
  --muted: #7183a8;
  --paper: #ffffff;
  --paper-deep: #fafcff;
  --line: #e4ebf7;
  --vermilion: #2f6fed;
  --teal: #5079cf;
  background: #f8fbff;
  color: #44536f;
}

.adjudication__topbar {
  height: 82px;
  padding: 0 24px;
  border-bottom: 1px solid #e8eef8;
  background: #ffffff;
  box-shadow: 0 8px 24px rgba(115, 137, 177, 0.08);
}
.adjudication__identity > span {
  writing-mode: horizontal-tb;
  padding: 4px 8px;
  border-radius: 8px;
  background: #e6efff;
  color: #2f6fed;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.05em;
  white-space: nowrap;
}
.adjudication__identity h2 { color: #31456f; font-family: 'Segoe UI', 'PingFang SC', sans-serif; font-size: 20px; }
.adjudication__identity p { color: #7183a8; }
.mode-switch { border: 1px solid #dce6f5; border-radius: 10px; overflow: hidden; }
.mode-switch a,
.mode-switch span { padding: 7px 12px; }
.mode-switch span { border: 0; background: #2f6fed; color: #ffffff; }
.adjudication__actions button,
.bulk-actions button { border-color: #dce6f5; border-radius: 10px; background: #ffffff; color: #506287; }
.adjudication__actions button:hover:not(:disabled),
.bulk-actions button:hover { border-color: #8fb3ff; background: #f0f6ff; color: #2f6fed; }
.adjudication__actions button.primary { border-color: #2f6fed; background: #2f6fed; color: #ffffff; }

.adjudication__body {
  height: calc(100vh - 82px);
  grid-template-columns: 190px minmax(380px, 1fr) 390px;
  gap: 16px;
  padding: 16px;
  background: #f8fbff;
}
.review-queue,
.source-review,
.difference-ledger {
  border: 0;
  border-radius: 18px;
  background: #ffffff;
  box-shadow: 0 14px 36px rgba(115, 137, 177, 0.12);
  overflow: hidden;
}
.review-queue > header { border-bottom-color: #eef2f8; background: #ffffff; }
.review-queue > header span { color: #31456f; }
.queue-item { width: calc(100% - 16px); margin: 5px 8px; border: 1px solid #e4ebf7; border-radius: 12px; background: #fafcff; }
.queue-item:hover { border-color: #8fb3ff; background: #f0f6ff; }
.queue-item.active { border-color: #2f6fed; background: #eef5ff; box-shadow: none; }
.queue-item strong { color: #31456f; font-family: 'Segoe UI', 'PingFang SC', sans-serif; }
.queue-item i { color: #8a97b3; }

.source-review__header { border-bottom-color: #e9eef8; background: #fafcff; }
.source-review__header > div:first-child span { color: #2f6fed; }
.source-review__header h3 { color: #31456f; font-family: 'Segoe UI', 'PingFang SC', sans-serif; }
.candidate-tabs button { border-bottom-color: #dce6f5; color: #7183a8; }
.candidate-tabs button.active { border-bottom-color: #2f6fed; color: #2f6fed; }
.source-review__counts strong { color: #2f6fed; font-family: 'Segoe UI', 'PingFang SC', sans-serif; }
.source-review__canvas { background: #ffffff; }
.source-review > footer { border-top-color: #e4ebf7; background: #fafcff; }
.source-review__empty > span { border-color: #b8cdf6; border-radius: 50%; background: #f5f8ff; color: #2f6fed; transform: none; }
.source-review__empty h3 { color: #31456f; font-family: 'Segoe UI', 'PingFang SC', sans-serif; }

.ledger-header { border-bottom-color: #e9eef8; background: #ffffff; }
.ledger-header div { color: #31456f; }
.ledger-header strong { color: #2f6fed; }
.ledger-filter button { border-bottom-color: #dce6f5; color: #7183a8; }
.ledger-filter button.active { border-bottom-color: #2f6fed; color: #2f6fed; }
.difference { border-color: #e4ebf7; border-radius: 12px; background: #ffffff; overflow: hidden; }
.difference[data-resolved='true'] { border-left-color: #2f6fed; }
.difference > header strong { color: #31456f; font-family: 'Segoe UI', 'PingFang SC', sans-serif; }
.difference > header span { color: #2f6fed; }
.candidate-values { border-top-color: #e9eef8; }
.candidate-values button { border-right-color: #e9eef8; }
.candidate-values button:hover { background: #f5f8ff; }
.candidate-values button.chosen { background: #eef5ff; }
.candidate-values b { color: #2f6fed; }
.manual-toggle { border-bottom-color: #8fb3ff; color: #2f6fed; }
.manual-editor textarea,
.manual-editor input,
.lock-note textarea { border-color: #dce6f5; border-radius: 10px; background: #ffffff; color: #44536f; }
.manual-editor textarea:focus,
.manual-editor input:focus,
.lock-note textarea:focus { border-color: #2f6fed; outline: none; box-shadow: 0 0 0 3px rgba(47, 111, 237, 0.1); }
.lock-note { border-top-color: #e4ebf7; }
.difference-ledger > footer { border-top-color: #e4ebf7; background: #fafcff; }

@media (max-width: 1250px) {
  .adjudication__body { grid-template-columns: 170px minmax(340px, 1fr) 350px; }
}
@media (max-width: 980px) {
  .adjudication__body { height: auto; grid-template-columns: 1fr; }
  .review-queue,
  .source-review,
  .difference-ledger { border-radius: 14px; }
}
</style>

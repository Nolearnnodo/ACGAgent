<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  type AIAnnotationReview,
  type AIAnnotationReviewItem,
  saveAIAnnotationReview,
  startAIAnnotationReview,
  submitAIAnnotationReview,
} from '../api/aiAnnotations'
import { getApiErrorMessage } from '../api/client'
import type { EvidenceSpan } from '../api/extractionAnnotations'
import AnnotationTextCanvas from '../components/annotation/AnnotationTextCanvas.vue'
import AppLayout from '../layouts/AppLayout.vue'
import {
  type AnnotationHighlight,
  evidenceGranularityIssue,
} from '../utils/extractionAnnotation'

const route = useRoute()
const jobId = computed(() => Number(route.params.jobId))
const review = ref<AIAnnotationReview | null>(null)
const loading = ref(true)
const saving = ref(false)
const submitting = ref(false)
const dirty = ref(false)
const feedback = ref('')
const activeItemId = ref<string | null>(null)
const activeOmissionId = ref<string | null>(null)
const activeEvidenceId = ref<string | null>(null)
const pendingEvidence = ref<EvidenceSpan | null>(null)
const categoryFilter = ref('全部')
const itemSearch = ref('')
const textCanvas = ref<InstanceType<typeof AnnotationTextCanvas> | null>(null)
let saveTimer: number | null = null
let saveQueued = false

const isReadOnly = computed(() => review.value?.status === 'submitted')

const liveSummary = computed(() => {
  const items = review.value?.items ?? []
  const correct = items.filter((item) => item.verdict === 'correct').length
  const incorrect = items.filter((item) => item.verdict === 'incorrect').length
  const unreviewed = items.length - correct - incorrect
  const omissions = review.value?.omissions.length ?? 0
  const denominator = correct + incorrect + omissions
  return {
    total: items.length,
    reviewed: correct + incorrect,
    correct,
    incorrect,
    unreviewed,
    omissions,
    accuracy: denominator ? correct / denominator : 0,
  }
})

const progressPercent = computed(() => (
  liveSummary.value.total
    ? Math.round(liveSummary.value.reviewed / liveSummary.value.total * 100)
    : 0
))

const categories = computed(() => [
  '全部',
  ...Array.from(new Set((review.value?.items ?? []).map((item) => item.category))),
])

const filteredItems = computed(() => {
  const keyword = itemSearch.value.trim().toLowerCase()
  return (review.value?.items ?? []).filter((item) => (
    (categoryFilter.value === '全部' || item.category === categoryFilter.value)
    && (!keyword || [
      item.title,
      item.question,
      item.detail,
      item.path,
      ...item.evidence.map((evidence) => evidence.quote),
    ].join(' ').toLowerCase().includes(keyword))
  ))
})

const activeItem = computed(() => (
  review.value?.items.find((item) => item.id === activeItemId.value) ?? null
))

const activeOmission = computed(() => (
  review.value?.omissions.find((item) => item.id === activeOmissionId.value) ?? null
))

const pendingEvidenceIssue = computed(() => evidenceGranularityIssue(pendingEvidence.value, 'fact'))

const relatedStandardIds = computed(() => new Set(activeItem.value?.standard_ids ?? []))

const highlights = computed<AnnotationHighlight[]>(() => {
  const values = new Map<string, AnnotationHighlight>()
  const kindByCategory: Record<string, AnnotationHighlight['kind']> = {
    人物: 'person',
    证据: 'person',
    事件: 'event',
    历史事件: 'history',
    关系: 'relation',
    异常: 'unresolved',
  }
  for (const item of review.value?.items ?? []) {
    for (const evidence of item.evidence) {
      if (values.has(evidence.id)) continue
      values.set(evidence.id, {
        id: evidence.id,
        evidence,
        kind: kindByCategory[item.category] ?? 'event',
        label: item.title,
        objectKey: item.id,
      })
    }
  }
  for (const omission of review.value?.omissions ?? []) {
    for (const evidence of omission.evidence) {
      if (values.has(evidence.id)) continue
      values.set(evidence.id, {
        id: evidence.id,
        evidence,
        kind: 'unresolved',
        label: `缺漏 · ${omission.description || omission.category}`,
        objectKey: omission.id,
      })
    }
  }
  return [...values.values()]
})

const submissionProblems = computed(() => {
  const problems: string[] = []
  if (liveSummary.value.unreviewed) problems.push(`还有 ${liveSummary.value.unreviewed} 项未判断`)
  const missingReasons = (review.value?.items ?? []).filter(
    (item) => item.verdict === 'incorrect' && !item.reason.trim(),
  ).length
  if (missingReasons) problems.push(`${missingReasons} 个错误项缺少理由`)
  const incompleteOmissions = (review.value?.omissions ?? []).filter((item) => (
    !item.target_path.trim()
    || !item.description.trim()
    || !item.correction.trim()
    || (!item.evidence.length && !item.evidence_note.trim())
  )).length
  if (incompleteOmissions) problems.push(`${incompleteOmissions} 条缺漏记录未补完整`)
  return problems
})

function uid(prefix: string) {
  return `${prefix}-${typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`
}

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

function isCoverageItem(item: AIAnnotationReviewItem) {
  return item.id.startsWith('coverage:')
}

function timeLabel(value: string | null) {
  if (!value) return '尚未保存'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function snapshot(value: AIAnnotationReview) {
  return JSON.stringify({
    items: value.items.map((item) => [item.id, item.verdict, item.reason]),
    omissions: value.omissions,
  })
}

async function loadReview() {
  loading.value = true
  feedback.value = ''
  review.value = null
  activeItemId.value = null
  activeOmissionId.value = null
  activeEvidenceId.value = null
  pendingEvidence.value = null
  try {
    if (!Number.isInteger(jobId.value) || jobId.value <= 0) throw new Error('质检任务编号无效。')
    review.value = await startAIAnnotationReview(jobId.value)
    activeItemId.value = review.value.items.find((item) => item.verdict === 'unreviewed')?.id
      ?? review.value.items[0]?.id
      ?? null
  } catch (error) {
    feedback.value = getApiErrorMessage(error, '首轮质检内容加载失败。')
  } finally {
    loading.value = false
  }
}

function markDirty() {
  if (isReadOnly.value) return
  dirty.value = true
  scheduleSave()
}

function scheduleSave() {
  if (saveTimer !== null) window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    saveTimer = null
    void saveDraft()
  }, 850)
}

async function saveDraft() {
  if (!review.value || isReadOnly.value || !dirty.value) return
  if (saving.value) {
    saveQueued = true
    return
  }
  saving.value = true
  const current = review.value
  const before = snapshot(current)
  dirty.value = false
  try {
    const next = await saveAIAnnotationReview(current.generation_job_id, current)
    if (review.value && snapshot(review.value) === before) {
      review.value = next
    } else {
      if (review.value) {
        review.value.id = next.id
        review.value.updated_at = next.updated_at
      }
      dirty.value = true
      saveQueued = true
    }
    feedback.value = '质检草稿已保存。'
  } catch (error) {
    dirty.value = true
    feedback.value = getApiErrorMessage(error, '质检草稿保存失败。')
  } finally {
    saving.value = false
    if (saveQueued) {
      saveQueued = false
      scheduleSave()
    }
  }
}

function setVerdict(item: AIAnnotationReviewItem, verdict: 'correct' | 'incorrect') {
  if (isReadOnly.value) return
  activeItemId.value = item.id
  item.verdict = verdict
  if (verdict === 'correct') item.reason = ''
  markDirty()
}

async function focusItem(item: AIAnnotationReviewItem) {
  activeItemId.value = item.id
  const evidence = item.evidence[0]
  if (!evidence) return
  await focusEvidence(evidence)
}

async function focusEvidence(evidence: EvidenceSpan) {
  activeEvidenceId.value = evidence.id
  await nextTick()
  textCanvas.value?.focusEvidence(evidence.id)
}

function activateHighlight(evidenceId: string) {
  activeEvidenceId.value = evidenceId
  const candidates = (review.value?.items ?? []).filter(
    (item) => item.evidence.some((evidence) => evidence.id === evidenceId),
  )
  const item = candidates.find((candidate) => candidate.path.includes('.mentions.'))
    ?? candidates[0]
  if (item) activeItemId.value = item.id
  const omission = review.value?.omissions.find(
    (candidate) => candidate.evidence.some((evidence) => evidence.id === evidenceId),
  )
  if (omission) activeOmissionId.value = omission.id
}

function handleSelectionCreated(evidence: EvidenceSpan) {
  pendingEvidence.value = evidence
  activeEvidenceId.value = null
}

function addOmission(evidence?: EvidenceSpan) {
  if (!review.value || isReadOnly.value) return
  const omission = {
    id: uid('omission'),
    category: '人物',
    target_path: '',
    description: '',
    correction: '',
    evidence: evidence ? [evidence] : [],
    evidence_note: '',
  }
  review.value.omissions.push(omission)
  activeOmissionId.value = omission.id
  pendingEvidence.value = null
  markDirty()
}

function appendPendingEvidence() {
  if (!activeOmission.value || !pendingEvidence.value || pendingEvidenceIssue.value) return
  if (!activeOmission.value.evidence.some((item) => item.id === pendingEvidence.value?.id)) {
    activeOmission.value.evidence.push(pendingEvidence.value)
  }
  pendingEvidence.value = null
  markDirty()
}

function removeOmission(omissionId: string) {
  if (!review.value || isReadOnly.value) return
  const index = review.value.omissions.findIndex((item) => item.id === omissionId)
  if (index >= 0) review.value.omissions.splice(index, 1)
  if (activeOmissionId.value === omissionId) {
    activeOmissionId.value = review.value.omissions[0]?.id ?? null
  }
  markDirty()
}

function removeOmissionEvidence(omissionId: string, evidenceId: string) {
  const omission = review.value?.omissions.find((item) => item.id === omissionId)
  if (!omission || isReadOnly.value) return
  omission.evidence = omission.evidence.filter((item) => item.id !== evidenceId)
  markDirty()
}

async function submitReview() {
  if (!review.value || submissionProblems.value.length || submitting.value) return
  if (!window.confirm('提交后质检记录将锁定，不能继续修改。确认提交吗？')) return
  if (saveTimer !== null) {
    window.clearTimeout(saveTimer)
    saveTimer = null
  }
  submitting.value = true
  feedback.value = ''
  try {
    review.value = await submitAIAnnotationReview(review.value.generation_job_id, review.value)
    dirty.value = false
    feedback.value = `首轮质检已提交，正确率 ${percent(review.value.summary.accuracy)}。`
  } catch (error) {
    feedback.value = getApiErrorMessage(error, '首轮质检提交失败。')
  } finally {
    submitting.value = false
  }
}

watch(
  () => route.params.jobId,
  () => {
    if (route.name === 'extraction-ai-annotation-review') void loadReview()
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  if (saveTimer !== null) window.clearTimeout(saveTimer)
  if (dirty.value && !isReadOnly.value) void saveDraft()
})
</script>

<template>
  <AppLayout variant="workspace">
    <div class="review-workbench">
      <header class="review-topbar">
        <div class="review-title">
          <RouterLink to="/annotations/extraction/ai" aria-label="返回 AI 标注工作台">←</RouterLink>
          <div>
            <span>AI FIRST-PASS REVIEW · 试用</span>
            <h2>{{ review?.passage_title ?? '首轮 AI 标注质检' }}</h2>
          </div>
        </div>
        <div v-if="review" class="review-progress">
          <div>
            <span>检查进度</span>
            <strong>{{ liveSummary.reviewed }} / {{ liveSummary.total }}</strong>
          </div>
          <i><b :style="{ width: `${progressPercent}%` }" /></i>
          <small>{{ progressPercent }}%</small>
        </div>
        <div v-if="review" class="accuracy-block">
          <span>首轮正确率</span>
          <strong>{{ percent(liveSummary.accuracy) }}</strong>
          <small>正确 ÷（正确 + 错误 + 缺漏）</small>
        </div>
        <div class="top-actions">
          <span v-if="review" class="state" :data-state="review.status">
            {{ review.status === 'submitted' ? '已锁定' : saving ? '保存中…' : dirty ? '待保存' : '草稿已保存' }}
          </span>
          <RouterLink to="/annotations/extraction/ai">返回工作台</RouterLink>
        </div>
      </header>

      <div v-if="loading" class="page-state">
        <b>正在组装检查清单</b>
        <p>检查对象固定为不可变的首轮 AI 生成结果。</p>
      </div>
      <div v-else-if="!review" class="page-state page-state--error">
        <b>无法进入质检</b>
        <p>{{ feedback || '首轮 AI 结果不存在或尚未完成。' }}</p>
        <RouterLink to="/annotations/extraction/ai">返回 AI 标注工作台</RouterLink>
      </div>

      <div v-else class="review-body">
        <section class="source-column">
          <header class="column-heading">
            <div>
              <span>01 / SOURCE EVIDENCE</span>
              <h3>原文与实体证据</h3>
            </div>
            <small>任务 #{{ review.task_id }} · 首轮 #{{ review.generation_job_id }}</small>
          </header>
          <div class="source-legend">
            <span><i data-kind="person" />人物</span>
            <span><i data-kind="event" />事件</span>
            <span><i data-kind="history" />历史事件</span>
            <span><i data-kind="relation" />关系</span>
            <span><i data-kind="unresolved" />缺漏</span>
          </div>
          <div class="source-scroll">
            <AnnotationTextCanvas
              ref="textCanvas"
              :text="review.passage_context"
              :highlights="highlights"
              :active-evidence-id="activeEvidenceId"
              :disabled="isReadOnly"
              @selection-created="handleSelectionCreated"
              @highlight-activated="activateHighlight"
            />
          </div>
          <Transition name="dock">
            <div v-if="pendingEvidence" class="evidence-dock">
              <button type="button" aria-label="取消选区" @click="pendingEvidence = null">×</button>
              <div>
                <span>缺漏证据 · {{ pendingEvidence.end - pendingEvidence.start }} 字</span>
                <strong>“{{ pendingEvidence.quote }}”</strong>
                <small v-if="pendingEvidenceIssue">{{ pendingEvidenceIssue }}</small>
              </div>
              <div>
                <button type="button" :disabled="Boolean(pendingEvidenceIssue)" @click="addOmission(pendingEvidence)">
                  新建缺漏记录
                </button>
                <button
                  type="button"
                  :disabled="!activeOmission || Boolean(pendingEvidenceIssue)"
                  @click="appendPendingEvidence"
                >加入当前缺漏</button>
              </div>
            </div>
          </Transition>
        </section>

        <section class="check-column">
          <header class="column-heading check-heading">
            <div>
              <span>02 / ITEM DECISIONS</span>
              <h3>逐项判断</h3>
            </div>
            <b>{{ liveSummary.unreviewed }} 未判断</b>
          </header>
          <div class="check-tools">
            <label>
              <span>搜索检查项</span>
              <input v-model="itemSearch" type="search" placeholder="人物、事件或原文证据" />
            </label>
            <nav aria-label="检查项分类">
              <button
                v-for="category in categories"
                :key="category"
                type="button"
                :class="{ active: categoryFilter === category }"
                @click="categoryFilter = category"
              >{{ category }}</button>
            </nav>
            <p class="offset-guide">
              <b>位置已自动计算</b>
              字符索引从 0 开始，区间采用左闭右开 [起点, 终点)；例如 70—72 覆盖索引 70、71。点击证据行即可定位原文。
            </p>
          </div>
          <div class="check-scroll">
            <p v-if="!filteredItems.length" class="empty">没有匹配的检查项。</p>
            <article
              v-for="(item, index) in filteredItems"
              :key="item.id"
              class="check-item"
              :class="{ active: activeItemId === item.id }"
              :data-verdict="item.verdict"
              @click="activeItemId = item.id"
            >
              <div class="check-item__index">{{ String(index + 1).padStart(2, '0') }}</div>
              <div class="check-item__body">
                <header>
                  <div>
                    <span>{{ item.category }} · {{ item.standard_ids.join(' / ') }}</span>
                    <h4>{{ item.title }}</h4>
                  </div>
                  <button v-if="item.evidence.length" type="button" @click.stop="focusItem(item)">
                    定位证据
                  </button>
                </header>
                <p>{{ item.question }}</p>
                <section class="ai-output" aria-label="AI 标注内容">
                  <header>
                    <span>AI 标注内容</span>
                    <code :title="item.path">字段：{{ item.path }}</code>
                  </header>
                  <p v-if="item.detail">{{ item.detail }}</p>
                  <p v-else-if="isCoverageItem(item)" class="ai-output__coverage">
                    此项检查首轮结果的全文覆盖情况，不对应单一字段值；请结合左侧原文判断是否存在漏标或误标。
                  </p>
                  <p v-else class="ai-output__coverage">AI 未填写额外属性；已提取的实体值与证据见下方。</p>
                </section>
                <div v-if="item.evidence.length" class="evidence-list" aria-label="AI 标注证据">
                  <button
                    v-for="evidence in item.evidence"
                    :key="evidence.id"
                    type="button"
                    :class="{ active: activeEvidenceId === evidence.id }"
                    :aria-label="`定位证据“${evidence.quote}”，字符区间 ${evidence.start} 到 ${evidence.end}`"
                    @click.stop="focusEvidence(evidence)"
                  >
                    <span class="evidence-list__quote">
                      <small>证据原文</small>
                      <strong>“{{ evidence.quote }}”</strong>
                    </span>
                    <span class="evidence-list__range">
                      <b>字符 {{ evidence.start }}—{{ evidence.end }}</b>
                      <small>{{ evidence.end - evidence.start }} 字 · UTF-16 {{ evidence.start_utf16 }}—{{ evidence.end_utf16 }}</small>
                    </span>
                    <span class="evidence-list__action">定位原文 →</span>
                  </button>
                </div>
                <p v-else-if="!isCoverageItem(item)" class="no-evidence">AI 未提供可直接定位的正文证据，请结合字段值与左侧全文核对。</p>
                <div class="verdict-buttons" role="group" :aria-label="`${item.title}判定`">
                  <button
                    type="button"
                    :class="{ selected: item.verdict === 'correct' }"
                    :disabled="isReadOnly"
                    @click.stop="setVerdict(item, 'correct')"
                  ><i>✓</i> 正确</button>
                  <button
                    type="button"
                    :class="{ selected: item.verdict === 'incorrect' }"
                    :disabled="isReadOnly"
                    @click.stop="setVerdict(item, 'incorrect')"
                  ><i>×</i> 错误</button>
                </div>
                <label v-if="item.verdict === 'incorrect'" class="reason-field" @click.stop>
                  <span>错误理由 <b>必填</b></span>
                  <textarea
                    v-model="item.reason"
                    :disabled="isReadOnly"
                    rows="2"
                    maxlength="2000"
                    placeholder="说明错在哪里，以及正确判断应是什么。"
                    @input="markDirty"
                  />
                </label>
              </div>
            </article>
          </div>
        </section>

        <aside class="review-rail">
          <div class="review-rail__scroll">
            <section class="score-section">
              <span>03 / REVIEW SUMMARY</span>
              <div class="score-ring" :style="{ '--score': `${liveSummary.accuracy * 360}deg` }">
                <strong>{{ percent(liveSummary.accuracy) }}</strong>
                <small>首轮正确率</small>
              </div>
              <dl>
                <div><dt>正确</dt><dd>{{ liveSummary.correct }}</dd></div>
                <div><dt>错误</dt><dd>{{ liveSummary.incorrect }}</dd></div>
                <div><dt>缺漏</dt><dd>{{ liveSummary.omissions }}</dd></div>
                <div><dt>未判断</dt><dd>{{ liveSummary.unreviewed }}</dd></div>
              </dl>
              <p>缺漏是 AI 首轮未生成的错误，因此计入正确率分母。</p>
            </section>

            <section class="omission-section">
              <header>
                <div>
                  <span>遗漏补录</span>
                  <strong>{{ review.omissions.length }}</strong>
                </div>
                <button type="button" :disabled="isReadOnly" @click="addOmission()">＋ 新建</button>
              </header>
              <p v-if="!review.omissions.length" class="empty">通读正文发现 AI 漏标时，在原文划选证据并建立记录。</p>
              <article
                v-for="(omission, index) in review.omissions"
                :key="omission.id"
                class="omission-card"
                :class="{ active: activeOmissionId === omission.id }"
                @click="activeOmissionId = omission.id"
              >
                <header>
                  <b>缺漏 {{ index + 1 }}</b>
                  <button type="button" :disabled="isReadOnly" @click.stop="removeOmission(omission.id)">删除</button>
                </header>
                <label>
                  <span>类别</span>
                  <select v-model="omission.category" :disabled="isReadOnly" @change="markDirty">
                    <option>人物</option>
                    <option>人物属性</option>
                    <option>生平事件</option>
                    <option>历史事件</option>
                    <option>人物关系</option>
                    <option>证据</option>
                    <option>排除或待定</option>
                    <option>其他</option>
                  </select>
                </label>
                <label>
                  <span>应补到哪个字段 <b>必填</b></span>
                  <input v-model.trim="omission.target_path" :disabled="isReadOnly" maxlength="255" placeholder="例如 persons / 任职 / person_relations" @input="markDirty" />
                </label>
                <label>
                  <span>AI 漏了什么 <b>必填</b></span>
                  <textarea v-model.trim="omission.description" :disabled="isReadOnly" rows="2" maxlength="2000" placeholder="说明漏标事实。" @input="markDirty" />
                </label>
                <label>
                  <span>应补录内容 <b>必填</b></span>
                  <textarea v-model.trim="omission.correction" :disabled="isReadOnly" rows="2" maxlength="4000" placeholder="写明应补入的实体、字段和值。" @input="markDirty" />
                </label>
                <div class="omission-evidence">
                  <span>原文证据</span>
                  <button
                    v-for="evidence in omission.evidence"
                    :key="evidence.id"
                    type="button"
                    @click.stop="activeEvidenceId = evidence.id; textCanvas?.focusEvidence(evidence.id)"
                  >
                    “{{ evidence.quote }}”
                    <i v-if="!isReadOnly" @click.stop="removeOmissionEvidence(omission.id, evidence.id)">×</i>
                  </button>
                  <small v-if="!omission.evidence.length">请在左侧原文划选；确实无法划选时填写下方说明。</small>
                </div>
                <label>
                  <span>无可划选证据时说明</span>
                  <textarea v-model.trim="omission.evidence_note" :disabled="isReadOnly" rows="2" maxlength="1000" placeholder="例如：事实仅见标题或原文残缺。" @input="markDirty" />
                </label>
              </article>
            </section>

            <section class="standard-section">
              <header>
                <div>
                  <span>完整检查规范</span>
                  <strong>{{ review.standards.length }} 条</strong>
                </div>
                <small>{{ review.standard_version }}</small>
              </header>
              <ol>
                <li
                  v-for="standard in review.standards"
                  :key="standard.id"
                  :class="{ related: relatedStandardIds.has(standard.id) }"
                >
                  <b>{{ standard.id }}</b>
                  <div>
                    <strong>{{ standard.title }}</strong>
                    <p>{{ standard.instruction }}</p>
                  </div>
                </li>
              </ol>
            </section>
          </div>

          <footer class="submit-bar">
            <p v-if="submissionProblems.length">{{ submissionProblems.join('；') }}。</p>
            <p v-else-if="review.status === 'submitted'">质检已于 {{ timeLabel(review.submitted_at) }} 锁定。</p>
            <p v-else>全部检查完成，可以锁定首轮质量记录。</p>
            <button
              type="button"
              :disabled="isReadOnly || Boolean(submissionProblems.length) || submitting"
              @click="submitReview"
            >{{ submitting ? '提交中…' : isReadOnly ? '质检已提交' : '提交首轮质检' }}</button>
            <small>{{ feedback || `最近保存：${timeLabel(review.updated_at)}` }}</small>
          </footer>
        </aside>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.review-workbench {
  --blue: #2f6fed;
  --blue-dark: #244f9e;
  --ink: #293b60;
  --muted: #7183a8;
  --line: #dde6f3;
  --paper: #fff;
  height: 100vh;
  overflow: hidden;
  background: #f5f8fd;
  color: #44536f;
}

.review-topbar {
  height: 78px;
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(230px, .7fr) 170px auto;
  align-items: center;
  gap: 24px;
  box-sizing: border-box;
  padding: 11px 22px;
  border-bottom: 1px solid #e1e8f3;
  background: #fff;
  box-shadow: 0 8px 24px rgb(70 97 145 / 7%);
}

.review-title { display: flex; align-items: center; gap: 12px; min-width: 0; }
.review-title > a { display: grid; width: 34px; height: 34px; place-items: center; border: 1px solid var(--line); border-radius: 9px; color: var(--blue); font-size: 18px; text-decoration: none; }
.review-title div { min-width: 0; }
.review-title span, .column-heading span, .score-section > span { color: var(--blue); font: 700 9px/1.2 ui-monospace, monospace; letter-spacing: .08em; }
.review-title h2 { overflow: hidden; margin: 4px 0 0; color: var(--ink); font-size: 18px; text-overflow: ellipsis; white-space: nowrap; }

.review-progress { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; }
.review-progress div { display: grid; }
.review-progress span, .accuracy-block span { color: var(--muted); font-size: 9px; }
.review-progress strong { color: var(--ink); font-size: 12px; }
.review-progress i { height: 5px; overflow: hidden; border-radius: 999px; background: #edf2fa; }
.review-progress i b { display: block; height: 100%; border-radius: inherit; background: var(--blue); transition: width 180ms ease; }
.review-progress small { color: var(--blue); font-size: 10px; }
.accuracy-block { display: grid; border-left: 1px solid var(--line); padding-left: 18px; }
.accuracy-block strong { color: var(--blue); font-size: 22px; line-height: 1; }
.accuracy-block small { margin-top: 3px; color: var(--muted); font-size: 8px; white-space: nowrap; }
.top-actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; }
.top-actions > a { border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px; color: var(--blue); font-size: 10px; text-decoration: none; }
.state { color: #8b6a29; font-size: 9px; white-space: nowrap; }
.state[data-state='submitted'] { color: #258660; }

.review-body { height: calc(100vh - 78px); display: grid; grid-template-columns: minmax(390px, 1.08fr) minmax(410px, .92fr) 350px; min-width: 0; }
.source-column, .check-column, .review-rail { min-width: 0; min-height: 0; position: relative; border-right: 1px solid var(--line); background: var(--paper); }
.check-column { display: grid; grid-template-rows: auto auto 1fr; background: #f9fbfe; }
.review-rail { display: grid; grid-template-rows: 1fr auto; border-right: 0; }
.column-heading { min-height: 67px; display: flex; align-items: center; justify-content: space-between; gap: 14px; box-sizing: border-box; padding: 14px 18px; border-bottom: 1px solid var(--line); }
.column-heading h3 { margin: 4px 0 0; color: var(--ink); font-size: 15px; }
.column-heading small { color: var(--muted); font: 9px ui-monospace, monospace; }
.check-heading b { color: #a2701f; font-size: 10px; }

.source-legend { height: 34px; display: flex; align-items: center; gap: 12px; padding: 0 18px; border-bottom: 1px solid #eef2f8; color: var(--muted); font-size: 9px; }
.source-legend span { display: flex; align-items: center; gap: 4px; }
.source-legend i { width: 7px; height: 7px; border-radius: 2px; background: #2f6fed; }
.source-legend i[data-kind='event'] { background: #3f7280; }
.source-legend i[data-kind='history'] { background: #78639a; }
.source-legend i[data-kind='relation'] { background: #9a702f; }
.source-legend i[data-kind='unresolved'] { background: #b56b2d; }
.source-scroll { height: calc(100% - 102px); overflow: auto; }

.evidence-dock { position: absolute; right: 16px; bottom: 16px; left: 16px; z-index: 3; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; padding: 12px 40px 12px 14px; border: 1px solid #bcd0f6; border-radius: 12px; background: #23375f; color: #fff; box-shadow: 0 14px 36px rgb(26 45 79 / 25%); }
.evidence-dock > button:first-child { position: absolute; top: 5px; right: 8px; border: 0; background: none; color: #b9c7e3; cursor: pointer; }
.evidence-dock > div { min-width: 0; display: grid; gap: 3px; }
.evidence-dock span { color: #aebedd; font-size: 9px; }
.evidence-dock strong { overflow: hidden; font: 500 13px 'Noto Serif SC', serif; text-overflow: ellipsis; white-space: nowrap; }
.evidence-dock small { color: #ffb8aa; font-size: 9px; }
.evidence-dock > div:last-child { display: flex; flex-direction: row; align-items: center; gap: 6px; }
.evidence-dock > div:last-child button { border: 1px solid #7196df; border-radius: 7px; padding: 7px 8px; background: #2f6fed; color: #fff; font-size: 9px; cursor: pointer; }
.evidence-dock > div:last-child button + button { background: transparent; }
.evidence-dock button:disabled { opacity: .4; cursor: not-allowed; }

.check-tools { display: grid; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); background: #fff; }
.check-tools label { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 9px; }
.check-tools label span { color: var(--muted); font-size: 9px; }
.check-tools input { min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px; outline: none; color: var(--ink); font-size: 10px; }
.check-tools input:focus { border-color: #9db9ef; box-shadow: 0 0 0 3px #eef4ff; }
.check-tools nav { display: flex; gap: 4px; overflow-x: auto; }
.check-tools nav button { flex: 0 0 auto; border: 0; border-radius: 7px; padding: 5px 8px; background: #f1f5fb; color: var(--muted); font-size: 9px; cursor: pointer; }
.check-tools nav button.active { background: #e4edff; color: var(--blue); font-weight: 700; }
.offset-guide { margin: 0; padding: 8px 9px; border-left: 2px solid #9bb8ee; background: #f6f9ff; color: #60708c; font-size: 9px; line-height: 1.55; }
.offset-guide b { margin-right: 5px; color: #365f9f; }
.check-scroll, .review-rail__scroll { min-height: 0; overflow: auto; }
.check-scroll { padding: 12px; }
.check-item { display: grid; grid-template-columns: 27px 1fr; margin-bottom: 9px; border: 1px solid var(--line); border-left: 3px solid #c8d3e4; border-radius: 10px; background: #fff; transition: border-color 140ms ease, box-shadow 140ms ease; }
.check-item:hover, .check-item.active { border-color: #afc6ef; box-shadow: 0 6px 18px rgb(73 101 153 / 8%); }
.check-item[data-verdict='correct'] { border-left-color: #3a9b70; }
.check-item[data-verdict='incorrect'] { border-left-color: #c85a61; }
.check-item__index { padding: 13px 0 0 8px; color: #9ba9bf; font: 9px ui-monospace, monospace; }
.check-item__body { min-width: 0; padding: 12px 12px 12px 5px; }
.check-item__body > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.check-item__body header span { color: var(--blue); font-size: 8px; font-weight: 700; }
.check-item__body h4 { margin: 3px 0 0; color: var(--ink); font-size: 12px; }
.check-item__body header > button { border: 0; background: none; color: var(--blue); font-size: 9px; cursor: pointer; }
.check-item__body > p { margin: 9px 0; color: #536482; font-size: 10px; line-height: 1.6; }
.ai-output { margin: 9px 0; padding: 9px 10px; border-left: 2px solid #8fafe9; background: #f6f8fc; }
.ai-output > header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.ai-output > header span { color: #365f9f; font-size: 9px; font-weight: 700; }
.ai-output code { min-width: 0; overflow-wrap: anywhere; color: #7a89a3; font: 8px/1.4 ui-monospace, monospace; text-align: right; }
.ai-output > p { margin: 6px 0 0; color: #394b69; font-size: 10px; line-height: 1.65; white-space: pre-wrap; }
.ai-output > p.ai-output__coverage { color: #6d7b92; }
.evidence-list { display: grid; gap: 5px; margin: 8px 0; }
.evidence-list > button { width: 100%; display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 10px; border: 1px solid #d8e3f5; border-radius: 8px; padding: 8px 9px; background: #fbfdff; color: #526c9c; text-align: left; cursor: pointer; transition: border-color 130ms ease, background 130ms ease, transform 130ms ease; }
.evidence-list > button:hover, .evidence-list > button.active { border-color: #8eb0ed; background: #f0f5ff; }
.evidence-list > button:hover { transform: translateY(-1px); }
.evidence-list__quote { min-width: 0; display: grid; gap: 3px; }
.evidence-list__quote small { color: #8190aa; font-size: 8px; }
.evidence-list__quote strong { overflow-wrap: anywhere; color: #263e66; font: 600 11px/1.45 'Noto Serif SC', serif; }
.evidence-list__range { display: grid; gap: 2px; padding-left: 10px; border-left: 1px solid #dbe5f5; white-space: nowrap; }
.evidence-list__range b { color: #315f9f; font: 700 10px/1.3 ui-monospace, monospace; }
.evidence-list__range small { color: #7a89a3; font: 8px/1.3 ui-monospace, monospace; }
.evidence-list__action { color: var(--blue); font-size: 8px; white-space: nowrap; }
.no-evidence { margin: 7px 0 0; color: #8a7753; font-size: 9px; line-height: 1.5; }
.omission-evidence > button { max-width: 100%; overflow: hidden; border: 1px solid #d8e3f5; border-radius: 6px; padding: 4px 6px; background: #f4f8ff; color: #526c9c; font-family: 'Noto Serif SC', serif; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.verdict-buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 10px; }
.verdict-buttons button { border: 1px solid var(--line); border-radius: 8px; padding: 7px; background: #fff; color: var(--muted); font-size: 10px; cursor: pointer; }
.verdict-buttons button:first-child.selected { border-color: #82c6a8; background: #eef9f4; color: #267653; }
.verdict-buttons button:last-child.selected { border-color: #e1a2a6; background: #fff3f3; color: #af414a; }
.verdict-buttons button:disabled { cursor: default; }
.verdict-buttons i { font-style: normal; font-weight: 800; }
.reason-field, .omission-card label { display: grid; gap: 4px; margin-top: 8px; }
.reason-field span, .omission-card label span, .omission-evidence > span { color: #687a9e; font-size: 9px; }
.reason-field b, .omission-card label b { color: #b74c55; }
.reason-field textarea, .omission-card textarea, .omission-card input, .omission-card select { width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 7px; padding: 7px 8px; outline: none; background: #fff; color: var(--ink); font: inherit; font-size: 10px; line-height: 1.5; resize: vertical; }
.reason-field textarea:focus, .omission-card textarea:focus, .omission-card input:focus, .omission-card select:focus { border-color: #9db9ef; box-shadow: 0 0 0 3px #eef4ff; }

.review-rail__scroll { padding: 14px; }
.score-section, .omission-section, .standard-section { padding-bottom: 16px; border-bottom: 1px solid var(--line); }
.score-ring { --score: 0deg; width: 116px; aspect-ratio: 1; display: grid; place-content: center; margin: 12px auto; border-radius: 50%; background: radial-gradient(circle at center, #fff 61%, transparent 62%), conic-gradient(var(--blue) var(--score), #e9eff8 0); text-align: center; }
.score-ring strong { color: var(--ink); font-size: 21px; }
.score-ring small { color: var(--muted); font-size: 8px; }
.score-section dl { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin: 0; }
.score-section dl div { padding: 7px 3px; border-radius: 7px; background: #f4f7fb; text-align: center; }
.score-section dt { color: var(--muted); font-size: 8px; }
.score-section dd { margin: 3px 0 0; color: var(--ink); font-size: 13px; font-weight: 700; }
.score-section p { margin: 9px 0 0; color: var(--muted); font-size: 9px; line-height: 1.55; }
.omission-section, .standard-section { padding-top: 15px; }
.omission-section > header, .standard-section > header, .omission-card > header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.omission-section > header div, .standard-section > header div { display: flex; align-items: baseline; gap: 7px; }
.omission-section > header span, .standard-section > header span { color: var(--ink); font-size: 11px; font-weight: 700; }
.omission-section > header strong, .standard-section > header strong { color: var(--blue); font-size: 10px; }
.omission-section > header button { border: 1px solid #cddbf2; border-radius: 7px; padding: 5px 7px; background: #f5f8ff; color: var(--blue); font-size: 9px; cursor: pointer; }
.empty { margin: 10px 0; color: var(--muted); font-size: 10px; line-height: 1.6; }
.omission-card { margin-top: 10px; padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: #fbfcff; }
.omission-card.active { border-color: #9db9ef; background: #f7faff; }
.omission-card > header b { color: var(--ink); font-size: 10px; }
.omission-card > header button { border: 0; background: none; color: #b54d55; font-size: 8px; cursor: pointer; }
.omission-evidence { display: grid; gap: 4px; margin-top: 8px; }
.omission-evidence > button { display: flex; align-items: center; justify-content: space-between; gap: 5px; }
.omission-evidence i { color: #b54d55; font-style: normal; }
.omission-evidence small { color: #9a7a3d; font-size: 8px; line-height: 1.5; }
.standard-section > header small { color: var(--muted); font: 8px ui-monospace, monospace; }
.standard-section ol { display: grid; gap: 7px; margin: 10px 0 0; padding: 0; list-style: none; }
.standard-section li { display: grid; grid-template-columns: 29px 1fr; gap: 7px; padding: 7px 5px; border-left: 2px solid transparent; }
.standard-section li.related { border-left-color: var(--blue); background: #f3f7ff; }
.standard-section li > b { color: #8ca0c2; font: 9px ui-monospace, monospace; }
.standard-section li strong { color: var(--ink); font-size: 10px; }
.standard-section li p { margin: 3px 0 0; color: var(--muted); font-size: 9px; line-height: 1.55; }

.submit-bar { padding: 12px 14px; border-top: 1px solid var(--line); background: #fff; box-shadow: 0 -8px 24px rgb(70 97 145 / 6%); }
.submit-bar p { margin: 0 0 8px; color: #8d6731; font-size: 9px; line-height: 1.5; }
.submit-bar button { width: 100%; border: 0; border-radius: 9px; padding: 9px; background: var(--blue); color: #fff; font-size: 10px; font-weight: 700; cursor: pointer; }
.submit-bar button:disabled { background: #bdc9df; cursor: not-allowed; }
.submit-bar small { display: block; overflow: hidden; margin-top: 6px; color: var(--muted); font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }

.page-state { height: calc(100vh - 78px); display: grid; place-content: center; text-align: center; }
.page-state b { color: var(--ink); font-size: 18px; }
.page-state p { color: var(--muted); }
.page-state a { color: var(--blue); }
.page-state--error b { color: #b54d55; }
.dock-enter-active, .dock-leave-active { transition: opacity 150ms ease, transform 150ms ease; }
.dock-enter-from, .dock-leave-to { opacity: 0; transform: translateY(8px); }

@media (max-width: 1450px) {
  .review-body { grid-template-columns: minmax(350px, 1fr) minmax(380px, 1fr) 310px; }
  .review-topbar { grid-template-columns: minmax(260px, 1fr) minmax(190px, .6fr) 150px auto; gap: 15px; }
}

@media (max-width: 1280px) {
  .review-workbench { height: auto; min-height: 100vh; overflow: visible; }
  .review-topbar { height: auto; grid-template-columns: 1fr auto; }
  .review-progress { order: 3; }
  .accuracy-block { order: 4; }
  .review-body { height: auto; grid-template-columns: 1fr; }
  .source-column { height: 70vh; }
  .check-column { height: 80vh; }
  .review-rail { min-height: 80vh; }
}
</style>

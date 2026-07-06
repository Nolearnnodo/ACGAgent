<script setup lang="ts">
import 'katex/dist/katex.min.css'

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AppLayout from '../layouts/AppLayout.vue'
import GraphViewer from '../components/GraphViewer.vue'
import { useAuthStore } from '../stores/auth'
import { renderReportMarkdown } from '../utils/reportRenderer'
import {
  adjudicateIdentity,
  annotateIdentity,
  generateIdentityReports,
  getReviewEvidence,
  listPendingReviews,
  type IdentityDecisionLog,
  type IdentityEvidenceResponse,
  type IdentityReviewItem,
} from '../api/review'

const route = useRoute()
const authStore = useAuthStore()
const annotationMode = computed(() => route.query.mode === 'annotation')
const canGenerateReports = computed(() => annotationMode.value && authStore.isAdmin)

const pendingItems = ref<IdentityReviewItem[]>([])
const loading = ref(false)
const activeItem = ref<IdentityReviewItem | null>(null)
const evidence = ref<IdentityEvidenceResponse | null>(null)
const evidenceLoading = ref(false)
const adjudicating = ref(false)
const reportGenerating = ref(false)
const reportMessage = ref('')
const humanConfidence = ref<number | null>(null)
const annotateNote = ref('')
const confidenceOptions = Array.from({ length: 11 }, (_, index) => index)
const renderedAiReport = computed(() => (
  evidence.value?.ai_report
    ? renderReportMarkdown(evidence.value.ai_report.report_markdown)
    : ''
))

interface PassageSegment {
  text: string
  matched: boolean
}

const DECISION_LABELS: Record<string, string> = {
  same: '判定同人',
  different: '判定不同人',
  insufficient: '证据不足',
}

function decisionLabel(decision: string): string {
  return DECISION_LABELS[decision] ?? decision
}

function decisionClass(decision: string): string {
  if (decision === 'same') return 'review-page__decision--same'
  if (decision === 'different') return 'review-page__decision--diff'
  return 'review-page__decision--unknown'
}

function hopLabel(log: IdentityDecisionLog): string {
  return log.used_full_text ? '全文终局裁定' : `第 ${log.hop} 跳`
}

function isActiveItem(item: IdentityReviewItem) {
  return (
    activeItem.value?.source_person_id === item.source_person_id &&
    activeItem.value?.target_person_id === item.target_person_id
  )
}

function getHighlightedPassageSegments(context: string, personName: string | null): PassageSegment[] {
  if (!personName) {
    return [{ text: context, matched: false }]
  }

  const segments: PassageSegment[] = []
  let searchStart = 0
  let matchIndex = context.indexOf(personName, searchStart)

  while (matchIndex !== -1) {
    if (matchIndex > searchStart) {
      segments.push({ text: context.slice(searchStart, matchIndex), matched: false })
    }
    segments.push({ text: personName, matched: true })
    searchStart = matchIndex + personName.length
    matchIndex = context.indexOf(personName, searchStart)
  }

  if (searchStart < context.length) {
    segments.push({ text: context.slice(searchStart), matched: false })
  }

  return segments.length ? segments : [{ text: context, matched: false }]
}

async function loadPending() {
  loading.value = true
  try {
    const resp = await listPendingReviews(annotationMode.value ? 'annotation' : 'pending')
    pendingItems.value = resp.items
  } finally {
    loading.value = false
  }
}

async function selectItem(item: IdentityReviewItem) {
  activeItem.value = item
  evidence.value = null
  evidenceLoading.value = true
  humanConfidence.value = null
  annotateNote.value = ''
  try {
    evidence.value = await getReviewEvidence(item.source_person_id, item.target_person_id)
  } finally {
    evidenceLoading.value = false
  }
}

async function handleGenerateReports() {
  if (!canGenerateReports.value || reportGenerating.value) return
  reportGenerating.value = true
  reportMessage.value = ''
  try {
    const result = await generateIdentityReports(false)
    reportMessage.value = `完成：共 ${result.pair_count} 对，新增 ${result.created_count} 份，跳过 ${result.skipped_count} 份，失败 ${result.failed_count} 份。`
    if (activeItem.value) {
      evidence.value = await getReviewEvidence(
        activeItem.value.source_person_id,
        activeItem.value.target_person_id,
      )
    }
  } catch (error) {
    reportMessage.value = '生成失败，请检查后端日志或管理员权限。'
    throw error
  } finally {
    reportGenerating.value = false
  }
}

// 标注模式：置信度（0~10）本身即方向信号，提交单一标注
async function handleAnnotateSubmit() {
  if (!activeItem.value) return
  if (humanConfidence.value == null) {
    alert('请先选择置信度（0~10）')
    return
  }
  adjudicating.value = true
  try {
    await annotateIdentity(
      activeItem.value.source_person_id,
      activeItem.value.target_person_id,
      humanConfidence.value,
      annotateNote.value,
    )
    activeItem.value.annotated = true
    activeItem.value = null
    evidence.value = null
  } finally {
    adjudicating.value = false
  }
}

// 管理员裁定模式：merge / keep_separate 直接改写图谱
async function handleAdjudicate(decision: string) {
  if (!activeItem.value) return
  adjudicating.value = true
  try {
    await adjudicateIdentity(
      activeItem.value.source_person_id,
      activeItem.value.target_person_id,
      decision,
    )
    activeItem.value = null
    evidence.value = null
    await loadPending()
  } finally {
    adjudicating.value = false
  }
}

onMounted(async () => {
  await loadPending()
})
</script>

<template>
  <AppLayout>
    <div class="review-page">
      <!-- 左侧列表 -->
      <section class="review-page__list">
        <h3>
          <template v-if="annotationMode">同名人物标注 ({{ pendingItems.length }})</template>
          <template v-else>待审核同名人物 ({{ pendingItems.length }})</template>
        </h3>
        <div v-if="canGenerateReports" class="review-page__report-tools">
          <button
            class="review-page__report-btn"
            type="button"
            :disabled="reportGenerating"
            @click="handleGenerateReports"
          >
            {{ reportGenerating ? '生成中...' : '一键生成 AI 参考报告' }}
          </button>
          <p v-if="reportMessage" class="review-page__report-message">{{ reportMessage }}</p>
        </div>
        <div v-if="loading" class="review-page__loading">加载中...</div>
        <div v-else-if="!pendingItems.length" class="review-page__empty">暂无待审核项</div>
        <button
          v-for="item in pendingItems"
          :key="`${item.source_person_id}-${item.target_person_id}`"
          class="review-page__item"
          :class="{
            'review-page__item--active': isActiveItem(item),
            'review-page__item--annotated': annotationMode && item.annotated,
          }"
          type="button"
          @click="selectItem(item)"
        >
          <div class="review-page__item-header">
            <strong>{{ item.source_name || item.source_person_id }} ↔ {{ item.target_name || item.target_person_id }}</strong>
            <span v-if="annotationMode && item.annotated" class="review-page__badge">已标注</span>
          </div>
          <!-- 标注模式隐藏系统置信度 -->
          <span v-if="!annotationMode" class="review-page__confidence">置信度: {{ (item.confidence * 100).toFixed(0) }}%</span>
          <span class="review-page__hops">
            <template v-if="item.hops != null">
              <template v-if="item.llm_used_full_text">LLM 全文裁定:</template>
              <template v-else>LLM {{ item.hops }} 跳判定:</template>
              <strong :class="item.llm_decision === 'same' ? 'review-page__decision--same' : 'review-page__decision--diff'">
                {{ item.llm_decision === 'same' ? '同人' : '不同人' }}
              </strong>
            </template>
            <template v-else>
              <span class="review-page__decision--unknown">LLM 无法判定</span>
            </template>
          </span>
          <span v-if="item.reason" class="review-page__reason">{{ item.reason }}</span>
        </button>
      </section>

      <!-- 右侧详情 -->
      <section class="review-page__detail">
        <div v-if="!activeItem" class="review-page__placeholder">请选择一项待审核记录</div>
        <template v-else>
          <header class="review-page__detail-header">
            <h3>{{ activeItem.source_name }} vs {{ activeItem.target_name }}</h3>
            <!-- 标注模式：置信度即方向信号，单一提交 -->
            <div v-if="annotationMode" class="review-page__annotation-controls">
              <div class="review-page__confidence-block">
                <span class="review-page__confidence-label">同人置信度（0~10）:</span>
                <div class="review-page__confidence-btns">
                  <button
                    v-for="n in confidenceOptions"
                    :key="n"
                    type="button"
                    class="review-page__conf-btn"
                    :class="{ 'review-page__conf-btn--active': humanConfidence === n }"
                    @click="humanConfidence = n"
                  >{{ n }}</button>
                </div>
                <div class="review-page__confidence-hint">
                  <span class="review-page__confidence-hint--low">0 = 非常确定不是同人</span>
                  <span class="review-page__confidence-hint--mid">5 = 存疑</span>
                  <span class="review-page__confidence-hint--high">10 = 非常确定是同人</span>
                </div>
              </div>
              <button
                class="review-page__merge-btn"
                :disabled="adjudicating"
                type="button"
                @click="handleAnnotateSubmit"
              >
                保存标注
              </button>
            </div>
            <!-- 管理员裁定模式：直接改写图谱 -->
            <div v-else class="review-page__actions">
              <button
                class="review-page__merge-btn"
                :disabled="adjudicating"
                type="button"
                @click="handleAdjudicate('merge')"
              >
                确认同人（合并）
              </button>
              <button
                class="review-page__separate-btn"
                :disabled="adjudicating"
                type="button"
                @click="handleAdjudicate('keep_separate')"
              >
                确认不同人（保留）
              </button>
            </div>
          </header>

          <!-- 标注备注 -->
          <div v-if="annotationMode" class="review-page__note-row">
            <input
              v-model="annotateNote"
              type="text"
              placeholder="备注（可选）"
              class="review-page__note-input"
            />
          </div>

          <!-- 图谱对比 -->
          <div v-if="evidenceLoading" class="review-page__loading">加载证据中...</div>
          <div v-else-if="evidence" class="review-page__evidence">
            <!-- 全局图谱 -->
            <div class="review-page__graph-section">
              <h4>证据图谱</h4>
              <GraphViewer :elements="evidence.graph_elements" height="450px" />
            </div>

            <!-- AI 考据报告 -->
            <div class="review-page__ai-report">
              <div class="review-page__ai-report-header">
                <h4>AI 考据报告</h4>
                <span v-if="evidence.ai_report?.updated_at">
                  更新于 {{ evidence.ai_report.updated_at }}
                </span>
              </div>
              <div
                v-if="evidence.ai_report"
                class="review-page__ai-report-body"
                v-html="renderedAiReport"
              />
              <p v-else class="review-page__ai-report-empty">
                暂无 AI 考据报告。管理员可在左侧一键生成全部同名人物参考报告。
              </p>
            </div>

            <!-- 证据摘要 -->
            <div class="review-page__summary-grid">
              <div class="review-page__summary-col">
                <h4>人物 A: {{ activeItem.source_name }} (ID: {{ activeItem.source_person_id }})</h4>
                <p><strong>文章来源:</strong> {{ activeItem.source_passages.join('、') || '无' }}</p>
                <div v-if="evidence.source_evidence">
                  <p><strong>生平事件:</strong> {{ (evidence.source_evidence as Record<string, unknown[]>).life_events?.length || 0 }} 条</p>
                  <p><strong>人物关系:</strong> {{ (evidence.source_evidence as Record<string, unknown[]>).relations?.length || 0 }} 条</p>
                </div>
              </div>
              <div class="review-page__summary-col">
                <h4>人物 B: {{ activeItem.target_name }} (ID: {{ activeItem.target_person_id }})</h4>
                <p><strong>文章来源:</strong> {{ activeItem.target_passages.join('、') || '无' }}</p>
                <div v-if="evidence.target_evidence">
                  <p><strong>生平事件:</strong> {{ (evidence.target_evidence as Record<string, unknown[]>).life_events?.length || 0 }} 条</p>
                  <p><strong>人物关系:</strong> {{ (evidence.target_evidence as Record<string, unknown[]>).relations?.length || 0 }} 条</p>
                </div>
              </div>
            </div>

            <!-- 审核关系详情（标注模式隐藏置信度） -->
            <div v-if="evidence.review_relation" class="review-page__review-detail">
              <h4>LLM 裁定记录</h4>
              <p v-if="!annotationMode"><strong>置信度:</strong> {{ evidence.review_relation.confidence }}</p>
              <p><strong>理由:</strong> {{ evidence.review_relation.reason }}</p>
              <pre v-if="evidence.review_relation.evidence" class="review-page__evidence-json">{{ JSON.stringify(evidence.review_relation.evidence, null, 2) }}</pre>
            </div>

            <!-- LLM 同名判断过程（逐轮留痕） -->
            <div v-if="evidence.decision_logs.length" class="review-page__review-detail">
              <h4>LLM 同名判断过程</h4>
              <div
                v-for="(log, index) in evidence.decision_logs"
                :key="index"
                class="review-page__decision-log"
              >
                <div class="review-page__decision-log-header">
                  <span class="review-page__decision-log-hop">{{ hopLabel(log) }}</span>
                  <strong :class="decisionClass(log.decision)">{{ decisionLabel(log.decision) }}</strong>
                  <span v-if="!annotationMode" class="review-page__decision-log-conf">
                    置信度 {{ (log.confidence * 100).toFixed(0) }}%
                  </span>
                </div>
                <p v-if="log.reason" class="review-page__decision-log-reason">
                  <strong>理由:</strong> {{ log.reason }}
                </p>
                <p v-if="log.positive_evidence.length" class="review-page__decision-log-ev">
                  <strong>支持同人:</strong> {{ log.positive_evidence.join('；') }}
                </p>
                <p v-if="log.negative_evidence.length" class="review-page__decision-log-ev">
                  <strong>反对同人:</strong> {{ log.negative_evidence.join('；') }}
                </p>
                <p v-if="log.missing_evidence.length" class="review-page__decision-log-ev">
                  <strong>缺失证据:</strong> {{ log.missing_evidence.join('；') }}
                </p>
              </div>
            </div>

            <!-- 原文对照 -->
            <div
              v-if="evidence.source_passage_texts.length || evidence.target_passage_texts.length"
              class="review-page__passage-compare"
            >
              <h4>原文对照</h4>
              <div class="review-page__passage-grid">
                <div class="review-page__passage-col">
                  <h5>人物 A 关联文章</h5>
                  <div
                    v-for="pt in evidence.source_passage_texts"
                    :key="pt.doc_id"
                    class="review-page__passage-block"
                  >
                    <div class="review-page__passage-title">{{ pt.title }} (doc_id: {{ pt.doc_id }})</div>
                    <pre class="review-page__passage-text"><template
                      v-for="(segment, index) in getHighlightedPassageSegments(pt.context, activeItem.source_name)"
                      :key="index"
                    ><strong
                      v-if="segment.matched"
                      class="review-page__passage-highlight"
                    >{{ segment.text }}</strong><template v-else>{{ segment.text }}</template></template></pre>
                  </div>
                  <p v-if="!evidence.source_passage_texts.length" class="review-page__passage-empty">无关联文章</p>
                </div>
                <div class="review-page__passage-col">
                  <h5>人物 B 关联文章</h5>
                  <div
                    v-for="pt in evidence.target_passage_texts"
                    :key="pt.doc_id"
                    class="review-page__passage-block"
                  >
                    <div class="review-page__passage-title">{{ pt.title }} (doc_id: {{ pt.doc_id }})</div>
                    <pre class="review-page__passage-text"><template
                      v-for="(segment, index) in getHighlightedPassageSegments(pt.context, activeItem.target_name)"
                      :key="index"
                    ><strong
                      v-if="segment.matched"
                      class="review-page__passage-highlight"
                    >{{ segment.text }}</strong><template v-else>{{ segment.text }}</template></template></pre>
                  </div>
                  <p v-if="!evidence.target_passage_texts.length" class="review-page__passage-empty">无关联文章</p>
                </div>
              </div>
            </div>
          </div>
        </template>
      </section>
    </div>
  </AppLayout>
</template>

<style scoped>
.review-page {
  display: grid;
  grid-template-columns: 350px 1fr;
  gap: 24px;
  height: calc(100vh - 140px);
}

.review-page__list,
.review-page__detail {
  background: #ffffff;
  border-radius: 20px;
  box-shadow: 0 14px 36px rgba(115, 137, 177, 0.12);
  padding: 20px;
  display: flex;
  flex-direction: column;
  overflow: auto;
}

.review-page__list h3,
.review-page__detail h3 {
  margin: 0 0 16px;
  color: #31456f;
}

.review-page__loading {
  padding: 12px;
  font-size: 14px;
  color: #7085b0;
  text-align: center;
}

.review-page__empty {
  padding: 24px;
  font-size: 14px;
  color: #9aa8c4;
  text-align: center;
}

.review-page__report-tools {
  border: 1px solid #e4ebf7;
  border-radius: 12px;
  background: #fafcff;
  padding: 12px;
  margin-bottom: 12px;
}

.review-page__report-btn {
  width: 100%;
  border: none;
  border-radius: 10px;
  background: #2f6fed;
  color: #ffffff;
  padding: 10px 12px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.review-page__report-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.review-page__report-message {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: #506287;
}

.review-page__placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  color: #9aa8c4;
}

.review-page__item {
  text-align: left;
  border: 1px solid #e4ebf7;
  border-radius: 14px;
  background: #fafcff;
  padding: 14px;
  margin-bottom: 10px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: border-color 0.15s, background 0.15s;
}

.review-page__item:hover {
  border-color: #8fb3ff;
  background: #f0f6ff;
}

.review-page__item--active {
  border-color: #2f6fed;
  background: #eef5ff;
}

.review-page__item--annotated {
  border-color: #7bc88c;
  background: #f4faf6;
}

.review-page__item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.review-page__item strong {
  color: #31456f;
  font-size: 14px;
}

.review-page__badge {
  font-size: 11px;
  background: #e3f8e8;
  color: #1e8a3c;
  padding: 2px 8px;
  border-radius: 8px;
  white-space: nowrap;
}

.review-page__confidence {
  font-size: 12px;
  color: #2f6fed;
}

.review-page__hops {
  font-size: 12px;
  color: #e08a2b;
}

.review-page__decision--same {
  color: #1e8a3c;
}

.review-page__decision--diff {
  color: #c44040;
}

.review-page__decision--unknown {
  color: #9aa8c4;
}

.review-page__reason {
  font-size: 12px;
  color: #8a97b3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review-page__detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.review-page__detail-header h3 {
  margin: 0;
}

.review-page__annotation-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.review-page__confidence-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.review-page__confidence-label {
  font-size: 13px;
  color: #506287;
  white-space: nowrap;
}

.review-page__confidence-btns {
  display: flex;
  gap: 4px;
}

.review-page__confidence-hint {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}

.review-page__confidence-hint--low {
  color: #c44040;
}

.review-page__confidence-hint--mid {
  color: #7085b0;
}

.review-page__confidence-hint--high {
  color: #1e8a3c;
}

.review-page__conf-btn {
  width: 28px;
  height: 28px;
  border: 1px solid #d0d8e8;
  border-radius: 8px;
  background: #fafcff;
  color: #506287;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
  transition: all 0.12s;
}

.review-page__conf-btn:hover {
  border-color: #8fb3ff;
  background: #f0f6ff;
}

.review-page__conf-btn--active {
  border-color: #2f6fed;
  background: #2f6fed;
  color: #fff;
}

.review-page__actions {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}

.review-page__merge-btn,
.review-page__separate-btn {
  border: none;
  border-radius: 12px;
  padding: 10px 16px;
  font-size: 13px;
  cursor: pointer;
  transition: opacity 0.15s;
}

.review-page__merge-btn {
  background: #e3f8e8;
  color: #1e8a3c;
}

.review-page__separate-btn {
  background: #e3edff;
  color: #2f6fed;
}

.review-page__merge-btn:disabled,
.review-page__separate-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.review-page__note-row {
  margin-bottom: 16px;
}

.review-page__note-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #e4ebf7;
  border-radius: 10px;
  font-size: 13px;
  color: #31456f;
  outline: none;
  transition: border-color 0.15s;
}

.review-page__note-input:focus {
  border-color: #2f6fed;
}

.review-page__evidence {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.review-page__graph-section h4,
.review-page__ai-report h4,
.review-page__review-detail h4 {
  margin: 0 0 10px;
  color: #31456f;
  font-size: 14px;
}

.review-page__ai-report {
  background: #fafcff;
  border: 1px solid #e4ebf7;
  border-radius: 12px;
  padding: 14px;
}

.review-page__ai-report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.review-page__ai-report-header span {
  font-size: 12px;
  color: #8a97b3;
}

.review-page__ai-report-body {
  margin: 0;
  background: #ffffff;
  border: 1px solid #e4ebf7;
  border-radius: 10px;
  padding: 12px;
  max-height: 520px;
  overflow: auto;
  white-space: normal;
  word-break: break-word;
  font-family: 'Noto Serif SC', 'Source Han Serif SC', 'SimSun', serif;
  font-size: 13px;
  line-height: 1.8;
  color: #31456f;
}

.review-page__ai-report-body :deep(h1),
.review-page__ai-report-body :deep(h2),
.review-page__ai-report-body :deep(h3),
.review-page__ai-report-body :deep(h4) {
  margin: 14px 0 8px;
  color: #31456f;
  line-height: 1.5;
}

.review-page__ai-report-body :deep(h1:first-child),
.review-page__ai-report-body :deep(h2:first-child),
.review-page__ai-report-body :deep(h3:first-child),
.review-page__ai-report-body :deep(h4:first-child) {
  margin-top: 0;
}

.review-page__ai-report-body :deep(p),
.review-page__ai-report-body :deep(ul),
.review-page__ai-report-body :deep(ol) {
  margin: 8px 0;
}

.review-page__ai-report-body :deep(ul),
.review-page__ai-report-body :deep(ol) {
  padding-left: 22px;
}

.review-page__ai-report-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-family: inherit;
  font-size: 12px;
}

.review-page__ai-report-body :deep(th),
.review-page__ai-report-body :deep(td) {
  border: 1px solid #dce5f3;
  padding: 7px 8px;
  vertical-align: top;
}

.review-page__ai-report-body :deep(th) {
  background: #f0f6ff;
  color: #31456f;
  font-weight: 700;
}

.review-page__ai-report-body :deep(code) {
  font-family: 'Fira Code', 'Consolas', monospace;
  background: #eef3fb;
  border-radius: 4px;
  padding: 1px 4px;
}

.review-page__ai-report-body :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  overflow-x: auto;
}

.review-page__ai-report-body :deep(pre code) {
  background: transparent;
  padding: 0;
}

.review-page__ai-report-body :deep(.katex-display) {
  overflow-x: auto;
  overflow-y: hidden;
  padding: 6px 0;
}

.review-page__ai-report-empty {
  margin: 0;
  font-size: 13px;
  color: #8a97b3;
  line-height: 1.7;
}

.review-page__summary-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.review-page__summary-col {
  background: #fafcff;
  border: 1px solid #e4ebf7;
  border-radius: 12px;
  padding: 14px;
}

.review-page__summary-col h4 {
  margin: 0 0 10px;
  color: #31456f;
  font-size: 13px;
}

.review-page__summary-col p {
  margin: 4px 0;
  font-size: 13px;
  color: #506287;
}

.review-page__summary-col strong {
  color: #42557f;
}

.review-page__review-detail {
  background: #fafcff;
  border: 1px solid #e4ebf7;
  border-radius: 12px;
  padding: 14px;
}

.review-page__review-detail p {
  margin: 4px 0;
  font-size: 13px;
  color: #506287;
}

.review-page__review-detail strong {
  color: #42557f;
}

.review-page__decision-log {
  border-top: 1px dashed #e0e7f3;
  padding: 10px 0 2px;
}

.review-page__decision-log:first-of-type {
  border-top: none;
  padding-top: 4px;
}

.review-page__decision-log-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  margin-bottom: 4px;
}

.review-page__decision-log-hop {
  font-weight: 600;
  color: #31456f;
}

.review-page__decision-log-conf {
  font-size: 12px;
  color: #7085b0;
}

.review-page__decision-log-reason,
.review-page__decision-log-ev {
  margin: 3px 0;
  font-size: 13px;
  color: #506287;
  line-height: 1.6;
}

.review-page__decision-log-reason strong,
.review-page__decision-log-ev strong {
  color: #42557f;
}

.review-page__evidence-json {
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  font-family: 'Fira Code', 'Consolas', monospace;
  overflow-x: auto;
  max-height: 300px;
  overflow-y: auto;
  margin: 8px 0 0;
  white-space: pre-wrap;
  word-break: break-all;
}

/* 原文对照 */
.review-page__passage-compare {
  background: #fafcff;
  border: 1px solid #e4ebf7;
  border-radius: 12px;
  padding: 14px;
}

.review-page__passage-compare h4 {
  margin: 0 0 12px;
  color: #31456f;
  font-size: 14px;
}

.review-page__passage-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.review-page__passage-col h5 {
  margin: 0 0 8px;
  color: #42557f;
  font-size: 13px;
}

.review-page__passage-block {
  margin-bottom: 12px;
}

.review-page__passage-title {
  font-size: 12px;
  font-weight: 600;
  color: #2f6fed;
  margin-bottom: 4px;
}

.review-page__passage-text {
  background: #f8f5ef;
  border: 1px solid #e8dcc8;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  font-family: 'Noto Serif SC', 'Source Han Serif SC', 'SimSun', serif;
  line-height: 1.8;
  color: #3d2e1a;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 400px;
  overflow-y: auto;
}

.review-page__passage-highlight {
  font-weight: 700;
  color: #1f2937;
}

.review-page__passage-empty {
  font-size: 13px;
  color: #9aa8c4;
}
</style>

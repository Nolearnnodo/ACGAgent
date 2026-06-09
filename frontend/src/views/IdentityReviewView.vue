<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AppLayout from '../layouts/AppLayout.vue'
import GraphViewer from '../components/GraphViewer.vue'
import {
  adjudicateIdentity,
  annotateIdentity,
  getReviewEvidence,
  listPendingReviews,
  type IdentityEvidenceResponse,
  type IdentityReviewItem,
} from '../api/review'

const route = useRoute()
const annotationMode = computed(() => route.query.mode === 'annotation')

const pendingItems = ref<IdentityReviewItem[]>([])
const loading = ref(false)
const activeItem = ref<IdentityReviewItem | null>(null)
const evidence = ref<IdentityEvidenceResponse | null>(null)
const evidenceLoading = ref(false)
const adjudicating = ref(false)
const humanConfidence = ref<number | null>(null)
const annotateNote = ref('')

interface PassageSegment {
  text: string
  matched: boolean
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

async function handleAdjudicate(decision: string) {
  if (!activeItem.value) return
  if (annotationMode.value && humanConfidence.value == null) {
    alert('请先选择人工置信度（1~10）')
    return
  }
  adjudicating.value = true
  try {
    if (annotationMode.value) {
      await annotateIdentity(
        activeItem.value.source_person_id,
        activeItem.value.target_person_id,
        decision,
        humanConfidence.value!,
        annotateNote.value,
      )
      activeItem.value.annotated = true
    } else {
      await adjudicateIdentity(
        activeItem.value.source_person_id,
        activeItem.value.target_person_id,
        decision,
      )
    }
    activeItem.value = null
    evidence.value = null
    if (!annotationMode.value) {
      await loadPending()
    }
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
              LLM {{ item.hops }} 跳判定:
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
            <div v-if="annotationMode" class="review-page__annotation-controls">
              <span class="review-page__confidence-label">人工置信度:</span>
              <div class="review-page__confidence-btns">
                <button
                  v-for="n in 10"
                  :key="n"
                  type="button"
                  class="review-page__conf-btn"
                  :class="{ 'review-page__conf-btn--active': humanConfidence === n }"
                  @click="humanConfidence = n"
                >{{ n }}</button>
              </div>
            </div>
            <div class="review-page__actions">
              <button
                class="review-page__merge-btn"
                :disabled="adjudicating"
                type="button"
                @click="handleAdjudicate('merge')"
              >
                确认同人{{ annotationMode ? '' : '（合并）' }}
              </button>
              <button
                class="review-page__separate-btn"
                :disabled="adjudicating"
                type="button"
                @click="handleAdjudicate('keep_separate')"
              >
                确认不同人{{ annotationMode ? '' : '（保留）' }}
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

.review-page__confidence-label {
  font-size: 13px;
  color: #506287;
  white-space: nowrap;
}

.review-page__confidence-btns {
  display: flex;
  gap: 4px;
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
.review-page__review-detail h4 {
  margin: 0 0 10px;
  color: #31456f;
  font-size: 14px;
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

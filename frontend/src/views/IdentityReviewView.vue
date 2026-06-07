<script setup lang="ts">
import { onMounted, ref } from 'vue'

import AppLayout from '../layouts/AppLayout.vue'
import GraphViewer from '../components/GraphViewer.vue'
import {
  adjudicateIdentity,
  getReviewEvidence,
  listPendingReviews,
  type IdentityEvidenceResponse,
  type IdentityReviewItem,
} from '../api/review'

const pendingItems = ref<IdentityReviewItem[]>([])
const loading = ref(false)
const activeItem = ref<IdentityReviewItem | null>(null)
const evidence = ref<IdentityEvidenceResponse | null>(null)
const evidenceLoading = ref(false)
const adjudicating = ref(false)

function isActiveItem(item: IdentityReviewItem) {
  return (
    activeItem.value?.source_person_id === item.source_person_id &&
    activeItem.value?.target_person_id === item.target_person_id
  )
}

async function loadPending() {
  loading.value = true
  try {
    const resp = await listPendingReviews()
    pendingItems.value = resp.items
  } finally {
    loading.value = false
  }
}

async function selectItem(item: IdentityReviewItem) {
  activeItem.value = item
  evidence.value = null
  evidenceLoading.value = true
  try {
    evidence.value = await getReviewEvidence(item.source_person_id, item.target_person_id)
  } finally {
    evidenceLoading.value = false
  }
}

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
        <h3>待审核同名人物 ({{ pendingItems.length }})</h3>
        <div v-if="loading" class="review-page__loading">加载中...</div>
        <div v-else-if="!pendingItems.length" class="review-page__empty">暂无待审核项</div>
        <button
          v-for="item in pendingItems"
          :key="`${item.source_person_id}-${item.target_person_id}`"
          class="review-page__item"
          :class="{ 'review-page__item--active': isActiveItem(item) }"
          type="button"
          @click="selectItem(item)"
        >
          <strong>{{ item.source_name || item.source_person_id }} ↔ {{ item.target_name || item.target_person_id }}</strong>
          <span class="review-page__confidence">置信度: {{ (item.confidence * 100).toFixed(0) }}%</span>
          <span class="review-page__reason">{{ item.reason }}</span>
        </button>
      </section>

      <!-- 右侧详情 -->
      <section class="review-page__detail">
        <div v-if="!activeItem" class="review-page__placeholder">请选择一项待审核记录</div>
        <template v-else>
          <header class="review-page__detail-header">
            <h3>{{ activeItem.source_name }} vs {{ activeItem.target_name }}</h3>
            <div class="review-page__actions">
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

            <!-- 审核关系详情 -->
            <div v-if="evidence.review_relation" class="review-page__review-detail">
              <h4>LLM 裁定记录</h4>
              <p><strong>置信度:</strong> {{ evidence.review_relation.confidence }}</p>
              <p><strong>理由:</strong> {{ evidence.review_relation.reason }}</p>
              <pre v-if="evidence.review_relation.evidence" class="review-page__evidence-json">{{ JSON.stringify(evidence.review_relation.evidence, null, 2) }}</pre>
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

.review-page__item strong {
  color: #31456f;
  font-size: 14px;
}

.review-page__confidence {
  font-size: 12px;
  color: #2f6fed;
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
}

.review-page__detail-header h3 {
  margin: 0;
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
</style>

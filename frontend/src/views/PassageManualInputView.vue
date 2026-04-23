<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from 'vue'

import AppLayout from '../layouts/AppLayout.vue'
import {
  createManualPassage,
  fetchPassageRuns,
  listPassages,
  type PassageDetail,
  type PassageExecutionRun,
  type PassageSummary,
} from '../api/passages'

const formState = reactive({
  title: '',
  context: '',
})
const createdPassage = ref<PassageDetail | null>(null)
const passages = ref<PassageSummary[]>([])
const activeDocId = ref<number | null>(null)
const activeRuns = ref<PassageExecutionRun[]>([])
const submitting = ref(false)
let pollingTimer: number | null = null

async function handleSubmit() {
  if (!formState.title.trim() || !formState.context.trim()) {
    return
  }

  submitting.value = true
  try {
    createdPassage.value = await createManualPassage({
      title: formState.title.trim(),
      context: formState.context.trim(),
    })
    passages.value = await listPassages()
    await openMonitor(createdPassage.value.doc_id)
    formState.title = ''
    formState.context = ''
  } finally {
    submitting.value = false
  }
}

async function openMonitor(docId: number) {
  activeDocId.value = docId
  await refreshRuns(docId)
  startPolling(docId)
}

async function refreshRuns(docId: number) {
  activeRuns.value = await fetchPassageRuns(docId)
}

function startPolling(docId: number) {
  stopPolling()
  pollingTimer = window.setInterval(async () => {
    await refreshRuns(docId)
    const latestRun = activeRuns.value[0]
    if (latestRun && ['success', 'failed'].includes(latestRun.status)) {
      stopPolling()
    }
  }, 3000)
}

function stopPolling() {
  if (pollingTimer !== null) {
    window.clearInterval(pollingTimer)
    pollingTimer = null
  }
}

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <AppLayout>
    <div class="manual-page">
      <section class="manual-card">
        <h3>古籍手工输入</h3>
        <p>输入文章标题与正文后，将触发固定 workflow 进行处理。</p>

        <form class="manual-form" @submit.prevent="handleSubmit">
          <label>
            标题
            <input v-model="formState.title" type="text" placeholder="请输入古籍标题" />
          </label>

          <label>
            正文
            <textarea
              v-model="formState.context"
              rows="12"
              placeholder="请输入古籍正文内容"
            />
          </label>

          <button type="submit" :disabled="submitting">
            {{ submitting ? '提交中...' : '提交并触发 workflow' }}
          </button>
        </form>
      </section>

      <section class="manual-card">
        <h3>最近一次提交与文章列表</h3>
        <div v-if="createdPassage">
          <strong>{{ createdPassage.title }}</strong>
          <p>状态：{{ createdPassage.workflow_status }}</p>
          <p>来源：{{ createdPassage.source_type }}</p>
        </div>
        <div v-if="passages.length" class="manual-passage-list">
          <button
            v-for="passage in passages"
            :key="passage.doc_id"
            class="manual-passage-button"
            :class="{ 'manual-passage-button--active': passage.doc_id === activeDocId }"
            type="button"
            @click="openMonitor(passage.doc_id)"
          >
            <strong>{{ passage.title }}</strong>
            <span>{{ passage.workflow_status }}</span>
          </button>
        </div>
      </section>

      <section class="manual-card">
        <h3>任务监控</h3>
        <div v-if="activeRuns.length" class="manual-run-list">
          <article v-for="run in activeRuns" :key="run.id" class="manual-run-item">
            <div class="manual-run-item__header">
              <strong>Run #{{ run.id }}</strong>
              <span>{{ run.status }}</span>
            </div>

            <div v-for="step in run.steps" :key="step.id" class="manual-step-item">
              <span>Step {{ step.step_no }} - {{ step.skill_code }}</span>
              <strong>{{ step.status }}</strong>
            </div>
          </article>
        </div>
        <p v-else>请选择一篇文章查看 workflow 状态。</p>
      </section>
    </div>
  </AppLayout>
</template>

<style scoped>
.manual-page {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr 1fr;
  gap: 24px;
}

.manual-card {
  background: #fff;
  border-radius: 20px;
  padding: 24px;
  box-shadow: 0 14px 36px rgba(115, 137, 177, 0.12);
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.manual-card h3 {
  margin: 0;
  color: #31456f;
}

.manual-card p {
  margin: 0;
  color: #7183a8;
}

.manual-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.manual-form label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: #4c6087;
  font-weight: 600;
}

.manual-form input,
.manual-form textarea {
  border: 1px solid #dce6f5;
  border-radius: 12px;
  padding: 12px 14px;
  font: inherit;
}

.manual-form button {
  width: fit-content;
  border: none;
  border-radius: 12px;
  padding: 10px 16px;
  background: #2f6fed;
  color: #fff;
  cursor: pointer;
}

.manual-passage-list,
.manual-run-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.manual-passage-button {
  text-align: left;
  border: 1px solid #e4ebf7;
  border-radius: 14px;
  background: #fafcff;
  padding: 14px;
  cursor: pointer;
}

.manual-passage-button--active {
  border-color: #8fb3ff;
  background: #eef5ff;
}

.manual-run-item {
  border: 1px solid #e9eef8;
  border-radius: 16px;
  padding: 16px;
}

.manual-run-item__header,
.manual-step-item {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.manual-step-item {
  padding-top: 10px;
  color: #526583;
}
</style>

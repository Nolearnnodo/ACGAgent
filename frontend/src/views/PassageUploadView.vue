<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

import AppLayout from '../layouts/AppLayout.vue'
import { fetchPassageRuns, listPassages, uploadPassages, type PassageDetail, type PassageExecutionRun, type PassageSummary } from '../api/passages'

const selectedFiles = ref<File[]>([])
const uploadedPassages = ref<PassageDetail[]>([])
const passages = ref<PassageSummary[]>([])
const activeDocId = ref<number | null>(null)
const activeRuns = ref<PassageExecutionRun[]>([])
const submitting = ref(false)
let pollingTimer: number | null = null

function handleFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  selectedFiles.value = Array.from(target.files ?? [])
}

async function handleUpload() {
  if (!selectedFiles.value.length) {
    return
  }

  submitting.value = true
  try {
    uploadedPassages.value = await uploadPassages(selectedFiles.value)
    passages.value = await listPassages()
    if (uploadedPassages.value.length) {
      await openMonitor(uploadedPassages.value[0].doc_id)
    }
    selectedFiles.value = []
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
    <div class="passage-page">
      <section class="passage-card">
        <h3>古籍上传</h3>
        <p>支持上传 `md` 与 `docx` 文件。首版按“每文件一次 workflow”执行。</p>

        <input type="file" multiple accept=".md,.docx" @change="handleFileChange" />
        <button type="button" :disabled="submitting || !selectedFiles.length" @click="handleUpload">
          {{ submitting ? '上传中...' : '开始上传' }}
        </button>

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
            <span>状态：{{ passage.workflow_status }}</span>
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
        <div v-if="activeRuns.length" class="passage-run-list">
          <article v-for="run in activeRuns" :key="run.id" class="passage-run-item">
            <div class="passage-run-item__header">
              <strong>Run #{{ run.id }}</strong>
              <span>{{ run.status }}</span>
            </div>

            <div v-for="step in run.steps" :key="step.id" class="passage-step-item">
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
.passage-page {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
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
}

.passage-monitor-button--active {
  border-color: #8fb3ff;
  background: #eef5ff;
}

.passage-run-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.passage-run-item {
  border: 1px solid #e9eef8;
  border-radius: 16px;
  padding: 16px;
}

.passage-run-item__header,
.passage-step-item {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.passage-step-item {
  padding-top: 10px;
  color: #526583;
}
</style>

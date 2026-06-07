<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { marked } from 'marked'

import axios from 'axios'

import AppLayout from '../layouts/AppLayout.vue'
import GraphViewer from '../components/GraphViewer.vue'
import {
  createConversation,
  deleteConversation,
  fetchConversationDetail,
  fetchMessageTrace,
  listConversations,
  renameConversation,
  sendMessage,
  type ConversationDetail,
  type ConversationItem,
  type MessageTrace,
} from '../api/chat'

marked.setOptions({
  breaks: true,
  gfm: true,
})

function renderMarkdown(content: string): string {
  return marked.parse(content) as string
}

const conversations = ref<ConversationItem[]>([])
const activeConversation = ref<ConversationDetail | null>(null)
const messageInput = ref('')
const sending = ref(false)
const renamingTitle = ref('')

const traceCache = reactive<Record<number, MessageTrace>>({})
const traceExpanded = reactive<Record<number, boolean>>({})
const traceLoading = reactive<Record<number, boolean>>({})
const traceDetailExpanded = reactive<Record<string, boolean>>({})

const canOperateConversation = computed(() => Boolean(activeConversation.value))

const SKILL_LABELS: Record<string, string> = {
  query_workflow: '查询工作流',
  conversation_reply_workflow: '对话回复',
  passage_ingestion_workflow: '古籍入库',
  query_intent_classifier_atomic: '意图分类',
  person_info_query_atomic: '人物信息查询',
  person_relation_query_atomic: '人物关系查询',
  graph_statistics_query_atomic: '图谱统计查询',
  query_answer_compose_atomic: '答案组装',
  graph_query_atomic: '图谱查询',
  graph_write_atomic: '图谱写入',
  conversation_reply_atomic: '对话回复',
  nl_to_cypher_read_atomic: 'NL→Cypher',
}

function skillLabel(code: string): string {
  return SKILL_LABELS[code] || code
}

function decisionTypeLabel(type: string): string {
  const map: Record<string, string> = { workflow: '工作流', atomic: '原子技能', reject: '拒绝' }
  return map[type] || type
}

function statusIcon(status: string): string {
  if (status === 'success') return '✅'
  if (status === 'failed') return '❌'
  return '⏳'
}

async function toggleTrace(messageId: number) {
  if (traceExpanded[messageId]) {
    traceExpanded[messageId] = false
    return
  }
  traceExpanded[messageId] = true

  if (traceCache[messageId]) return
  if (!activeConversation.value) return

  traceLoading[messageId] = true
  try {
    traceCache[messageId] = await fetchMessageTrace(activeConversation.value.id, messageId)
  } catch {
    traceExpanded[messageId] = false
  } finally {
    traceLoading[messageId] = false
  }
}

function toggleDetail(key: string) {
  traceDetailExpanded[key] = !traceDetailExpanded[key]
}

async function loadConversations() {
  conversations.value = await listConversations()
  if (!conversations.value.length) {
    const conversation = await createConversation('默认对话')
    conversations.value = [conversation]
  }
  await openConversation(conversations.value[0].id)
}

async function openConversation(conversationId: number) {
  activeConversation.value = await fetchConversationDetail(conversationId)
  renamingTitle.value = activeConversation.value.title
}

async function createNewConversation() {
  const title = `对话 ${conversations.value.length + 1}`
  const conversation = await createConversation(title)
  conversations.value.unshift(conversation)
  await openConversation(conversation.id)
}

async function handleRenameConversation() {
  if (!activeConversation.value || !renamingTitle.value.trim()) {
    return
  }

  const updated = await renameConversation(activeConversation.value.id, renamingTitle.value.trim())
  activeConversation.value = {
    ...activeConversation.value,
    title: updated.title,
    updated_at: updated.updated_at,
  }
  conversations.value = conversations.value.map((conversation) =>
    conversation.id === updated.id ? updated : conversation,
  )
}

async function handleDeleteConversation() {
  if (!activeConversation.value) {
    return
  }

  const conversationId = activeConversation.value.id
  await deleteConversation(conversationId)
  conversations.value = conversations.value.filter((conversation) => conversation.id !== conversationId)

  if (!conversations.value.length) {
    const fallbackConversation = await createConversation('默认对话')
    conversations.value = [fallbackConversation]
  }

  await openConversation(conversations.value[0].id)
}

async function handleSendMessage() {
  if (!activeConversation.value || !messageInput.value.trim()) {
    return
  }

  const content = messageInput.value.trim()
  messageInput.value = ''
  sending.value = true

  const tempUserMsg: typeof activeConversation.value.messages[0] = {
    id: Date.now(),
    role: 'user',
    content,
    sequence: (activeConversation.value.messages.length + 1),
    created_at: new Date().toISOString(),
  }
  activeConversation.value.messages.push(tempUserMsg)

  try {
    activeConversation.value = await sendMessage(activeConversation.value.id, content)
    conversations.value = await listConversations()
  } catch (err: unknown) {
    let errorText = '服务暂时不可用，请稍后重试'

    if (axios.isAxiosError(err)) {
      if (err.code === 'ECONNABORTED' || err.message?.includes('timeout')) {
        errorText = '请求超时，请稍后重试'
      } else if (err.response) {
        const detail = err.response.data?.detail
        if (detail) {
          errorText = typeof detail === 'string' ? detail : JSON.stringify(detail)
        }
      } else if (err.request) {
        errorText = '网络连接失败，请稍后重试'
      }
    }

    if (activeConversation.value) {
      activeConversation.value.messages.push({
        id: Date.now(),
        role: 'error' as 'assistant',
        content: errorText,
        sequence: activeConversation.value.messages.length + 1,
        created_at: new Date().toISOString(),
      })
    }
  } finally {
    sending.value = false
  }
}

onMounted(async () => {
  await loadConversations()
})
</script>

<template>
  <AppLayout>
    <div class="chat-page">
      <section class="chat-page__list">
        <div class="chat-page__list-header">
          <h3>对话列表</h3>
          <button type="button" @click="createNewConversation">新建</button>
        </div>

        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          class="chat-page__conversation"
          :class="{ 'chat-page__conversation--active': conversation.id === activeConversation?.id }"
          type="button"
          @click="openConversation(conversation.id)"
        >
          <strong>{{ conversation.title }}</strong>
          <span>{{ new Date(conversation.updated_at).toLocaleString() }}</span>
        </button>
      </section>

      <section class="chat-page__detail">
        <header class="chat-page__detail-header">
          <div class="chat-page__detail-title">
            <input
              v-model="renamingTitle"
              class="chat-page__title-input"
              type="text"
              placeholder="请输入对话标题"
              :disabled="!canOperateConversation"
            />
            <p>Planner 将根据当前输入与短期上下文选择已有 Skill 或 Workflow。</p>
          </div>
          <div class="chat-page__detail-actions">
            <button type="button" class="chat-page__secondary-button" :disabled="!canOperateConversation" @click="handleRenameConversation">
              重命名
            </button>
            <button type="button" class="chat-page__danger-button" :disabled="!canOperateConversation" @click="handleDeleteConversation">
              删除
            </button>
          </div>
        </header>

        <div class="chat-page__messages">
          <div
            v-for="message in activeConversation?.messages ?? []"
            :key="message.id"
            class="chat-page__message-group"
          >
            <div
              class="chat-page__message"
              :class="{
                'chat-page__message--assistant': message.role === 'assistant',
                'chat-page__message--user': message.role === 'user',
                'chat-page__message--error': message.role === 'error',
              }"
            >
              <span class="chat-page__message-role">{{ message.role === 'error' ? '错误' : message.role }}</span>
              <div v-if="message.role === 'assistant'" class="chat-page__message-content" v-html="renderMarkdown(message.content)" />
              <p v-else>{{ message.content }}</p>
            </div>

            <!-- 推理过程追踪面板 -->
            <div v-if="message.role === 'assistant'" class="trace-panel">
              <button class="trace-panel__toggle" type="button" @click="toggleTrace(message.id)">
                <span class="trace-panel__toggle-icon" :class="{ 'trace-panel__toggle-icon--open': traceExpanded[message.id] }">&#9654;</span>
                查看推理过程
              </button>

              <div v-if="traceLoading[message.id]" class="trace-panel__loading">加载中...</div>

              <div v-if="traceExpanded[message.id] && traceCache[message.id]" class="trace-panel__body">

                <!-- 图谱可视化 -->
                <div v-if="traceCache[message.id]?.graph_elements?.nodes?.length" class="trace-section">
                  <button class="trace-section__header" type="button" @click="toggleDetail(`graph-${message.id}`)">
                    <span class="trace-section__icon" :class="{ 'trace-section__icon--open': traceDetailExpanded[`graph-${message.id}`] }">&#9654;</span>
                    <span class="trace-section__badge trace-section__badge--orange">图谱</span>
                    <span class="trace-section__summary">
                      {{ traceCache[message.id].graph_elements!.nodes.length }} 节点 · {{ traceCache[message.id].graph_elements!.edges.length }} 关系
                    </span>
                  </button>
                  <div v-if="traceDetailExpanded[`graph-${message.id}`]" class="trace-section__detail trace-section__detail--graph">
                    <GraphViewer :elements="traceCache[message.id].graph_elements!" height="360px" />
                  </div>
                </div>

                <!-- 意图识别 -->
                <div v-if="traceCache[message.id].planner" class="trace-section">
                  <button class="trace-section__header" type="button" @click="toggleDetail(`planner-${message.id}`)">
                    <span class="trace-section__icon" :class="{ 'trace-section__icon--open': traceDetailExpanded[`planner-${message.id}`] }">&#9654;</span>
                    <span class="trace-section__badge trace-section__badge--blue">意图识别</span>
                    <span class="trace-section__summary">
                      {{ traceCache[message.id].planner!.intent }}
                      &rarr; {{ skillLabel(traceCache[message.id].planner!.target_skill_code) }}
                    </span>
                  </button>
                  <div v-if="traceDetailExpanded[`planner-${message.id}`]" class="trace-section__detail">
                    <div class="trace-kv"><span class="trace-kv__key">意图</span><span class="trace-kv__val">{{ traceCache[message.id].planner!.intent }}</span></div>
                    <div class="trace-kv"><span class="trace-kv__key">决策类型</span><span class="trace-kv__val">{{ decisionTypeLabel(traceCache[message.id].planner!.decision_type) }}</span></div>
                    <div class="trace-kv"><span class="trace-kv__key">目标技能</span><span class="trace-kv__val">{{ skillLabel(traceCache[message.id].planner!.target_skill_code) }}</span></div>
                    <div class="trace-kv"><span class="trace-kv__key">推理理由</span><span class="trace-kv__val">{{ traceCache[message.id].planner!.reason }}</span></div>
                  </div>
                </div>

                <!-- 执行步骤 -->
                <div v-if="traceCache[message.id].steps.length" class="trace-section">
                  <button class="trace-section__header" type="button" @click="toggleDetail(`steps-${message.id}`)">
                    <span class="trace-section__icon" :class="{ 'trace-section__icon--open': traceDetailExpanded[`steps-${message.id}`] }">&#9654;</span>
                    <span class="trace-section__badge trace-section__badge--green">执行步骤</span>
                    <span class="trace-section__summary">
                      {{ traceCache[message.id].steps.length }} 步 {{ statusIcon(traceCache[message.id].execution_status || '') }}
                    </span>
                  </button>
                  <div v-if="traceDetailExpanded[`steps-${message.id}`]" class="trace-section__detail">
                    <div v-for="step in traceCache[message.id].steps" :key="step.step_no" class="trace-step">
                      <div class="trace-step__header">
                        <span class="trace-step__no">Step {{ step.step_no }}</span>
                        <span class="trace-step__skill">{{ skillLabel(step.skill_code) }}</span>
                        <span class="trace-step__status">{{ statusIcon(step.status) }}</span>
                      </div>
                      <div v-if="step.output_preview" class="trace-step__output">
                        <button class="trace-detail-toggle" type="button" @click="toggleDetail(`step-out-${message.id}-${step.step_no}`)">
                          {{ traceDetailExpanded[`step-out-${message.id}-${step.step_no}`] ? '收起输出' : '查看输出' }}
                        </button>
                        <pre v-if="traceDetailExpanded[`step-out-${message.id}-${step.step_no}`]" class="trace-code">{{ JSON.stringify(step.output_preview, null, 2) }}</pre>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 图谱查询证据链 -->
                <div v-if="traceCache[message.id].tool_calls.length" class="trace-section">
                  <button class="trace-section__header" type="button" @click="toggleDetail(`tools-${message.id}`)">
                    <span class="trace-section__icon" :class="{ 'trace-section__icon--open': traceDetailExpanded[`tools-${message.id}`] }">&#9654;</span>
                    <span class="trace-section__badge trace-section__badge--teal">证据链</span>
                    <span class="trace-section__summary">
                      {{ traceCache[message.id].tool_calls.length }} 次图谱查询
                    </span>
                  </button>
                  <div v-if="traceDetailExpanded[`tools-${message.id}`]" class="trace-section__detail">
                    <div v-for="(tc, idx) in traceCache[message.id].tool_calls" :key="idx" class="trace-tool-call">
                      <div class="trace-tool-call__header">
                        <span class="trace-tool-call__idx">#{{ idx + 1 }}</span>
                        <span class="trace-tool-call__name">{{ tc.tool_name }}</span>
                        <span class="trace-tool-call__meta">{{ tc.latency_ms }}ms</span>
                        <span class="trace-tool-call__status">{{ statusIcon(tc.status) }}</span>
                      </div>
                      <!-- Cypher 输入 -->
                      <div v-if="tc.input_preview" class="trace-tool-call__sub">
                        <button class="trace-detail-toggle" type="button" @click="toggleDetail(`tool-in-${message.id}-${idx}`)">
                          {{ traceDetailExpanded[`tool-in-${message.id}-${idx}`] ? '收起 Cypher' : '查看 Cypher' }}
                        </button>
                        <pre v-if="traceDetailExpanded[`tool-in-${message.id}-${idx}`]" class="trace-code trace-code--cypher">{{ (tc.input_preview as Record<string, unknown>).cypher || JSON.stringify(tc.input_preview, null, 2) }}</pre>
                      </div>
                      <!-- 查询结果 -->
                      <div v-if="tc.output_preview" class="trace-tool-call__sub">
                        <button class="trace-detail-toggle" type="button" @click="toggleDetail(`tool-out-${message.id}-${idx}`)">
                          {{ traceDetailExpanded[`tool-out-${message.id}-${idx}`] ? '收起结果' : '查看结果' }}
                        </button>
                        <pre v-if="traceDetailExpanded[`tool-out-${message.id}-${idx}`]" class="trace-code">{{ JSON.stringify(tc.output_preview, null, 2) }}</pre>
                      </div>
                      <div v-if="tc.error_message" class="trace-tool-call__error">{{ tc.error_message }}</div>
                    </div>
                  </div>
                </div>

                <!-- LLM 调用链 -->
                <div v-if="traceCache[message.id].llm_calls.length" class="trace-section">
                  <button class="trace-section__header" type="button" @click="toggleDetail(`llm-${message.id}`)">
                    <span class="trace-section__icon" :class="{ 'trace-section__icon--open': traceDetailExpanded[`llm-${message.id}`] }">&#9654;</span>
                    <span class="trace-section__badge trace-section__badge--purple">LLM 调用</span>
                    <span class="trace-section__summary">
                      {{ traceCache[message.id].llm_calls.length }} 次调用
                    </span>
                  </button>
                  <div v-if="traceDetailExpanded[`llm-${message.id}`]" class="trace-section__detail">
                    <div v-for="(call, idx) in traceCache[message.id].llm_calls" :key="idx" class="trace-llm-call">
                      <div class="trace-llm-call__header">
                        <span class="trace-llm-call__idx">#{{ idx + 1 }}</span>
                        <span class="trace-llm-call__skill">{{ skillLabel(call.skill_code) }}</span>
                        <span class="trace-llm-call__meta">{{ call.model }} | {{ call.total_tokens }} tokens | {{ call.latency_ms }}ms</span>
                        <span class="trace-llm-call__status">{{ statusIcon(call.status) }}</span>
                      </div>
                      <div v-if="call.call_purpose" class="trace-llm-call__purpose">用途: {{ call.call_purpose }}</div>
                      <button class="trace-detail-toggle" type="button" @click="toggleDetail(`llm-resp-${message.id}-${idx}`)">
                        {{ traceDetailExpanded[`llm-resp-${message.id}-${idx}`] ? '收起响应' : '查看响应' }}
                      </button>
                      <pre v-if="traceDetailExpanded[`llm-resp-${message.id}-${idx}`]" class="trace-code">{{ call.response_text }}</pre>
                    </div>
                  </div>
                </div>

                <!-- 执行摘要 -->
                <div v-if="traceCache[message.id].summary" class="trace-section trace-summary-bar">
                  <div class="trace-summary-bar__items">
                    <span class="trace-summary-bar__item">LLM {{ traceCache[message.id].summary!.llm_call_count }} 次</span>
                    <span v-if="traceCache[message.id].summary!.tool_call_count" class="trace-summary-bar__sep">|</span>
                    <span v-if="traceCache[message.id].summary!.tool_call_count" class="trace-summary-bar__item">查询 {{ traceCache[message.id].summary!.tool_call_count }} 次</span>
                    <span class="trace-summary-bar__sep">|</span>
                    <span class="trace-summary-bar__item">{{ traceCache[message.id].summary!.total_tokens }} tokens</span>
                    <span class="trace-summary-bar__sep">|</span>
                    <span class="trace-summary-bar__item">{{ traceCache[message.id].summary!.total_latency_ms }}ms</span>
                    <span v-if="traceCache[message.id].summary!.estimated_total_cost > 0" class="trace-summary-bar__sep">|</span>
                    <span v-if="traceCache[message.id].summary!.estimated_total_cost > 0" class="trace-summary-bar__item">
                      {{ traceCache[message.id].summary!.estimated_total_cost.toFixed(4) }} {{ traceCache[message.id].summary!.currency }}
                    </span>
                  </div>
                </div>

                <!-- 无追踪数据 -->
                <div
                  v-if="!traceCache[message.id].planner && !traceCache[message.id].steps.length && !traceCache[message.id].llm_calls.length && !traceCache[message.id].tool_calls.length"
                  class="trace-panel__empty"
                >
                  暂无推理过程数据
                </div>
              </div>
            </div>
          </div>

          <div v-if="sending" class="chat-page__message chat-page__message--assistant chat-page__message--loading">
            <span class="chat-page__message-role">assistant</span>
            <div class="chat-page__loading-dots">
              <span></span><span></span><span></span>
            </div>
          </div>
        </div>

        <form class="chat-page__composer" @submit.prevent="handleSendMessage">
          <textarea
            v-model="messageInput"
            placeholder="请输入任务，例如：查询某个节点的关系"
            rows="4"
          />
          <button type="submit" :disabled="sending">{{ sending ? '发送中...' : '发送' }}</button>
        </form>
      </section>
    </div>
  </AppLayout>
</template>

<style scoped>
.chat-page {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 24px;
  height: calc(100vh - 140px);
}

.chat-page__list,
.chat-page__detail {
  background: #ffffff;
  border-radius: 20px;
  box-shadow: 0 14px 36px rgba(115, 137, 177, 0.12);
  padding: 20px;
  display: flex;
  flex-direction: column;
}

.chat-page__list-header,
.chat-page__detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.chat-page__list-header h3,
.chat-page__detail-header h3 {
  margin: 0;
  color: #31456f;
}

.chat-page__detail-header p {
  margin: 0;
  color: #7183a8;
  font-size: 14px;
}

.chat-page__detail-title {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.chat-page__title-input {
  border: 1px solid #dce6f5;
  border-radius: 12px;
  padding: 12px 14px;
  font-size: 20px;
  font-weight: 700;
  color: #31456f;
  background: #fff;
}

.chat-page__detail-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.chat-page__list-header button,
.chat-page__composer button {
  border: none;
  border-radius: 12px;
  padding: 10px 14px;
  background: #2f6fed;
  color: #fff;
  cursor: pointer;
}

.chat-page__secondary-button {
  border: none;
  border-radius: 12px;
  padding: 10px 14px;
  background: #e9f1ff;
  color: #2f6fed;
  cursor: pointer;
}

.chat-page__danger-button {
  border: none;
  border-radius: 12px;
  padding: 10px 14px;
  background: #ffe7e7;
  color: #d44343;
  cursor: pointer;
}

.chat-page__conversation {
  text-align: left;
  border: 1px solid #e4ebf7;
  border-radius: 14px;
  background: #fafcff;
  padding: 14px;
  margin-bottom: 12px;
  cursor: pointer;
}

.chat-page__conversation strong {
  display: block;
  color: #42557f;
}

.chat-page__conversation span {
  display: block;
  margin-top: 8px;
  color: #8a97b3;
  font-size: 12px;
}

.chat-page__conversation--active {
  border-color: #8fb3ff;
  background: #eef5ff;
}

.chat-page__messages {
  flex: 1;
  overflow: auto;
  padding: 8px 4px 20px;
}

.chat-page__message-group {
  margin-bottom: 16px;
}

.chat-page__message {
  max-width: 78%;
  border-radius: 16px;
  padding: 14px 16px;
}

.chat-page__message p {
  margin: 8px 0 0;
  white-space: pre-wrap;
  line-height: 1.6;
}

.chat-page__message-content {
  margin: 8px 0 0;
  line-height: 1.7;
}

.chat-page__message-content :deep(p) {
  margin: 0.5em 0;
}

.chat-page__message-content :deep(p:first-child) {
  margin-top: 0;
}

.chat-page__message-content :deep(h1),
.chat-page__message-content :deep(h2),
.chat-page__message-content :deep(h3),
.chat-page__message-content :deep(h4) {
  margin: 0.8em 0 0.4em;
  color: #31456f;
}

.chat-page__message-content :deep(h1) { font-size: 1.4em; }
.chat-page__message-content :deep(h2) { font-size: 1.2em; }
.chat-page__message-content :deep(h3) { font-size: 1.05em; }

.chat-page__message-content :deep(ul),
.chat-page__message-content :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.5em;
}

.chat-page__message-content :deep(li) {
  margin: 0.25em 0;
}

.chat-page__message-content :deep(code) {
  background: #e8edf6;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 0.9em;
  font-family: 'Fira Code', 'Consolas', monospace;
}

.chat-page__message-content :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 10px;
  padding: 14px 16px;
  overflow-x: auto;
  margin: 0.6em 0;
}

.chat-page__message-content :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
  font-size: 0.85em;
}

.chat-page__message-content :deep(blockquote) {
  border-left: 3px solid #8fb3ff;
  margin: 0.6em 0;
  padding: 4px 12px;
  color: #506080;
  background: #f0f5ff;
  border-radius: 0 8px 8px 0;
}

.chat-page__message-content :deep(table) {
  border-collapse: collapse;
  margin: 0.6em 0;
  width: 100%;
}

.chat-page__message-content :deep(th),
.chat-page__message-content :deep(td) {
  border: 1px solid #dce6f5;
  padding: 8px 12px;
  text-align: left;
}

.chat-page__message-content :deep(th) {
  background: #f0f5ff;
  font-weight: 600;
}

.chat-page__message-content :deep(a) {
  color: #2f6fed;
  text-decoration: none;
}

.chat-page__message-content :deep(hr) {
  border: none;
  border-top: 1px solid #e4ebf7;
  margin: 1em 0;
}

.chat-page__message-role {
  font-size: 12px;
  text-transform: uppercase;
  color: #7085b0;
}

.chat-page__message--user {
  margin-left: auto;
  background: #eaf2ff;
}

.chat-page__message--assistant {
  background: #f5f7fb;
}

.chat-page__message--error {
  background: #fff0f0;
  color: #c0392b;
}

.chat-page__message--error .chat-page__message-role {
  color: #c0392b;
}

.chat-page__message--error p {
  color: #c0392b;
}

.chat-page__loading-dots {
  display: flex;
  gap: 6px;
  padding: 8px 0;
}

.chat-page__loading-dots span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #7085b0;
  animation: dot-bounce 1.4s infinite ease-in-out both;
}

.chat-page__loading-dots span:nth-child(1) { animation-delay: 0s; }
.chat-page__loading-dots span:nth-child(2) { animation-delay: 0.2s; }
.chat-page__loading-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes dot-bounce {
  0%, 80%, 100% { transform: scale(0.4); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

.chat-page__composer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chat-page__composer textarea {
  width: 100%;
  resize: vertical;
  border: 1px solid #dce6f5;
  border-radius: 14px;
  padding: 14px;
  box-sizing: border-box;
  font-family: inherit;
}

/* ── 推理过程追踪面板 ── */

.trace-panel {
  max-width: 78%;
  margin-top: 4px;
}

.trace-panel__toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: none;
  background: none;
  color: #7085b0;
  font-size: 12px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 8px;
  transition: background 0.15s;
}

.trace-panel__toggle:hover {
  background: #f0f4fa;
  color: #4a6091;
}

.trace-panel__toggle-icon {
  display: inline-block;
  font-size: 10px;
  transition: transform 0.2s;
}

.trace-panel__toggle-icon--open {
  transform: rotate(90deg);
}

.trace-panel__loading {
  padding: 8px 12px;
  font-size: 12px;
  color: #7085b0;
}

.trace-panel__body {
  margin-top: 6px;
  border: 1px solid #e4ebf7;
  border-radius: 12px;
  background: #fafcff;
  padding: 10px;
}

.trace-panel__empty {
  padding: 8px 0;
  font-size: 13px;
  color: #9aa8c4;
  text-align: center;
}

/* ── 追踪分区 ── */

.trace-section {
  margin-bottom: 6px;
}

.trace-section:last-child {
  margin-bottom: 0;
}

.trace-section__header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: none;
  cursor: pointer;
  padding: 6px 4px;
  border-radius: 8px;
  text-align: left;
  font-size: 13px;
  color: #42557f;
  transition: background 0.15s;
}

.trace-section__header:hover {
  background: #eef3fb;
}

.trace-section__icon {
  display: inline-block;
  font-size: 9px;
  color: #7085b0;
  transition: transform 0.2s;
  flex-shrink: 0;
}

.trace-section__icon--open {
  transform: rotate(90deg);
}

.trace-section__badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
  font-weight: 600;
  flex-shrink: 0;
}

.trace-section__badge--blue { background: #e3edff; color: #2f6fed; }
.trace-section__badge--green { background: #e3f8e8; color: #1e8a3c; }
.trace-section__badge--purple { background: #efe3ff; color: #7c3aed; }
.trace-section__badge--teal { background: #e0f5f0; color: #0f766e; }
.trace-section__badge--orange { background: #fff3e0; color: #e65100; }

.trace-section__summary {
  color: #6b7fa3;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace-section__detail {
  margin: 4px 0 4px 20px;
  padding: 8px 10px;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #eef2fa;
}

.trace-section__detail--graph {
  padding: 0;
  border: none;
  background: none;
}

/* ── 键值对 ── */

.trace-kv {
  display: flex;
  gap: 8px;
  padding: 3px 0;
  font-size: 13px;
  line-height: 1.5;
}

.trace-kv__key {
  color: #7085b0;
  flex-shrink: 0;
  min-width: 70px;
}

.trace-kv__val {
  color: #31456f;
  word-break: break-all;
}

/* ── 展开/收起按钮 ── */

.trace-detail-toggle {
  border: none;
  background: none;
  color: #7085b0;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}

.trace-detail-toggle:hover {
  background: #eef3fb;
  color: #4a6091;
}

/* ── 代码块 ── */

.trace-code {
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  font-family: 'Fira Code', 'Consolas', monospace;
  overflow-x: auto;
  max-height: 300px;
  overflow-y: auto;
  margin: 4px 0 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.trace-code--cypher {
  background: #1a2332;
  color: #93c5fd;
}

/* ── 执行步骤 ── */

.trace-step {
  padding: 6px 0;
  border-bottom: 1px solid #f0f4fa;
}

.trace-step:last-child {
  border-bottom: none;
}

.trace-step__header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.trace-step__no {
  font-weight: 600;
  color: #506080;
}

.trace-step__skill {
  color: #2f6fed;
}

.trace-step__status {
  margin-left: auto;
}

.trace-step__output {
  margin-top: 4px;
}

/* ── LLM 调用 ── */

.trace-llm-call {
  padding: 6px 0;
  border-bottom: 1px solid #f0f4fa;
}

.trace-llm-call:last-child {
  border-bottom: none;
}

.trace-llm-call__header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}

.trace-llm-call__idx {
  font-weight: 600;
  color: #7c3aed;
}

.trace-llm-call__skill {
  color: #42557f;
}

.trace-llm-call__meta {
  color: #8a97b3;
  font-size: 11px;
}

.trace-llm-call__status {
  margin-left: auto;
}

.trace-llm-call__purpose {
  font-size: 12px;
  color: #6b7fa3;
  margin: 2px 0 4px 24px;
}

/* ── 图谱查询 ── */

.trace-tool-call {
  padding: 6px 0;
  border-bottom: 1px solid #f0f4fa;
}

.trace-tool-call:last-child {
  border-bottom: none;
}

.trace-tool-call__header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}

.trace-tool-call__idx {
  font-weight: 600;
  color: #0f766e;
}

.trace-tool-call__name {
  color: #42557f;
}

.trace-tool-call__meta {
  color: #8a97b3;
  font-size: 11px;
}

.trace-tool-call__status {
  margin-left: auto;
}

.trace-tool-call__sub {
  margin-top: 4px;
  margin-left: 24px;
}

.trace-tool-call__error {
  margin-top: 4px;
  margin-left: 24px;
  font-size: 12px;
  color: #dc2626;
}

/* ── 执行摘要条 ── */

.trace-summary-bar {
  border-top: 1px solid #e4ebf7;
  padding-top: 8px;
}

.trace-summary-bar__items {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #6b7fa3;
  flex-wrap: wrap;
}

.trace-summary-bar__item {
  white-space: nowrap;
}

.trace-summary-bar__sep {
  color: #c8d4e8;
}
</style>

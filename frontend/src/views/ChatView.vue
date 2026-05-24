<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { marked } from 'marked'

import axios from 'axios'

import AppLayout from '../layouts/AppLayout.vue'
import {
  createConversation,
  deleteConversation,
  fetchConversationDetail,
  listConversations,
  renameConversation,
  sendMessage,
  type ConversationDetail,
  type ConversationItem,
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

const canOperateConversation = computed(() => Boolean(activeConversation.value))

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

.chat-page__message {
  max-width: 78%;
  border-radius: 16px;
  padding: 14px 16px;
  margin-bottom: 16px;
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
</style>

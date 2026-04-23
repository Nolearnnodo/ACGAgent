<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

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

  sending.value = true
  try {
    activeConversation.value = await sendMessage(activeConversation.value.id, messageInput.value.trim())
    messageInput.value = ''
    conversations.value = await listConversations()
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
            :class="message.role === 'assistant' ? 'chat-page__message--assistant' : 'chat-page__message--user'"
          >
            <span class="chat-page__message-role">{{ message.role }}</span>
            <p>{{ message.content }}</p>
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

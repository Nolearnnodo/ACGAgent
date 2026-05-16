import apiClient from './client'

export interface ChatMessage {
  id: number
  role: string
  content: string
  sequence: number
  created_at: string
}

export interface ConversationItem {
  id: number
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationDetail extends ConversationItem {
  messages: ChatMessage[]
}

export async function listConversations() {
  const { data } = await apiClient.get<ConversationItem[]>('/chat/conversations')
  return data
}

export async function createConversation(title: string) {
  const { data } = await apiClient.post<ConversationItem>('/chat/conversations', { title })
  return data
}

export async function fetchConversationDetail(conversationId: number) {
  const { data } = await apiClient.get<ConversationDetail>(`/chat/conversations/${conversationId}`)
  return data
}

export async function renameConversation(conversationId: number, title: string) {
  const { data } = await apiClient.patch<ConversationItem>(`/chat/conversations/${conversationId}`, {
    title,
  })
  return data
}

export async function deleteConversation(conversationId: number) {
  const { data } = await apiClient.delete<{ message: string }>(`/chat/conversations/${conversationId}`)
  return data
}

export async function sendMessage(conversationId: number, content: string) {
  const { data } = await apiClient.post<ConversationDetail>(
    `/chat/conversations/${conversationId}/messages`,
    { content },
  )
  return data
}

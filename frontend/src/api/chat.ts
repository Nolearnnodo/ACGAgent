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

export interface PlannerDecision {
  intent: string
  decision_type: string
  target_skill_code: string
  reason: string
}

export interface ExecutionStep {
  step_no: number
  skill_code: string
  status: string
  output_preview: Record<string, unknown> | null
}

export interface LLMCallSummary {
  skill_code: string
  call_purpose: string
  provider: string
  model: string
  total_tokens: number
  latency_ms: number
  status: string
  response_text: string
}

export interface ToolCallSummary {
  skill_code: string
  tool_name: string
  input_preview: Record<string, unknown> | null
  output_preview: Record<string, unknown> | null
  latency_ms: number
  status: string
  error_message: string
}

export interface TraceSummary {
  llm_call_count: number
  tool_call_count: number
  total_tokens: number
  estimated_total_cost: number
  currency: string
  total_latency_ms: number
}

export interface MessageTrace {
  planner: PlannerDecision | null
  execution_status: string | null
  execution_started_at: string | null
  execution_finished_at: string | null
  steps: ExecutionStep[]
  llm_calls: LLMCallSummary[]
  tool_calls: ToolCallSummary[]
  summary: TraceSummary | null
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

export async function fetchMessageTrace(conversationId: number, messageId: number) {
  const { data } = await apiClient.get<MessageTrace>(
    `/chat/conversations/${conversationId}/messages/${messageId}/trace`,
  )
  return data
}

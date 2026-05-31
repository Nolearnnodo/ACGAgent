import apiClient from './client'

export interface PassageSummary {
  doc_id: number
  title: string
  source_type: string
  workflow_status: string
  updated_at: string
}

export interface PassageDetail extends PassageSummary {
  context: string
  file_name: string | null
  created_by: number
  created_at: string
}

export interface PassageUploadResult extends PassageDetail {
  upload_status: 'queued' | 'skipped_existing'
  skip_reason: string | null
}

export interface PassageStepRun {
  id: number
  step_no: number
  skill_code: string
  status: string
  input_json: string
  output_json: string
  error_message: string
  created_at: string
}

export interface PassageExecutionRun {
  id: number
  trigger_type: string
  status: string
  started_at: string
  finished_at: string | null
  steps: PassageStepRun[]
}

export interface PassageLLMCallUsage {
  id: number
  execution_run_id: number | null
  execution_step_run_id: number | null
  passage_id: number | null
  skill_code: string
  provider: string
  model: string
  status: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  prompt_cache_hit_tokens: number
  prompt_cache_miss_tokens: number
  cache_hit_ratio: number
  cache_metrics_supported: boolean
  estimated_total_cost: number
  currency: string
  error_message: string
  created_at: string
}

export interface PassageTraceSummary {
  execution_run_id: number
  llm_call_count: number
  tool_call_count: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  prompt_cache_hit_tokens: number
  prompt_cache_miss_tokens: number
  cache_hit_ratio: number
  estimated_input_cost: number
  estimated_output_cost: number
  estimated_total_cost: number
  currency: string
  total_latency_ms: number
  failed_call_count: number
  updated_at: string
}

export interface PassageTokenUsage {
  doc_id: number
  execution_run_id: number | null
  summary: PassageTraceSummary | null
  llm_calls: PassageLLMCallUsage[]
}

export async function uploadPassages(files: File[]) {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }

  const { data } = await apiClient.post<PassageUploadResult[]>('/passages/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    timeout: 60000,
  })
  return data
}

export async function createManualPassage(payload: { title: string; context: string }) {
  const { data } = await apiClient.post<PassageDetail>('/passages/manual', payload)
  return data
}

export async function listPassages() {
  const { data } = await apiClient.get<PassageSummary[]>('/passages')
  return data
}

export async function fetchPassage(docId: number) {
  const { data } = await apiClient.get<PassageDetail>(`/passages/${docId}`)
  return data
}

export async function fetchPassageRuns(docId: number) {
  const { data } = await apiClient.get<PassageExecutionRun[]>(`/passages/${docId}/runs`)
  return data
}

export async function fetchPassageTokenUsage(docId: number) {
  const { data } = await apiClient.get<PassageTokenUsage>(`/passages/${docId}/token-usage`)
  return data
}

export interface PassageUsageOverviewItem {
  doc_id: number
  title: string
  workflow_status: string
  llm_call_count: number
  total_tokens: number
  prompt_cache_hit_tokens: number
  prompt_cache_miss_tokens: number
  cache_hit_ratio: number
  estimated_total_cost: number
  currency: string
}

export interface PassageUsageOverview {
  items: PassageUsageOverviewItem[]
  total_llm_calls: number
  total_tokens: number
  total_cost: number
  currency: string
}

export async function fetchPassageUsageOverview() {
  const { data } = await apiClient.get<PassageUsageOverview>('/passages/token-usage-overview')
  return data
}

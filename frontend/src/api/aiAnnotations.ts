import apiClient from './client'
import type { ExtractionAnnotationLabel } from './extractionAnnotations'

export interface AIAnnotationConfig {
  provider: '' | 'mock' | 'deepseek' | 'openai_compatible'
  api_key: string
  base_url: string
  model: string
  temperature: number | null
  extra_instruction: string
}

export interface AIAnnotationIssue {
  path: string
  message: string
  code: string
}

export interface AIAnnotationExportDocument {
  metadata: {
    task_id: number
    spec_version: string
    context_sha256: string
  }
  passage: {
    doc_id: number
    title: string
    context: string
    context_sha256: string
  }
  label: ExtractionAnnotationLabel
}

export interface AIAnnotationTaskContext {
  task_id: number
  context_sha256: string
  spec_version: string
  status: string
  passage: {
    doc_id: number
    title: string
    context: string
    source_type: string
    workflow_status: string
  }
}

export interface AIAnnotationPrompt {
  task_id: number
  prompt_version: string
  system_prompt: string
  user_prompt: string
}

export interface AIAnnotationPromptOverrides {
  system_prompt: string
  user_prompt: string
}

export type AIAnnotationValidationStatus = 'valid' | 'invalid_format' | 'invalid_rules'

export interface AIAnnotationTokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  prompt_cache_hit_tokens: number
  prompt_cache_miss_tokens: number
  cache_metrics_supported: boolean
  llm_call_count: number
}

export interface AIAnnotationResult {
  task_id: number
  passage_id: number
  passage_title: string
  spec_version: string
  context_sha256: string
  source: 'generated' | 'validated'
  provider: string
  model: string
  raw_content: string
  document: AIAnnotationExportDocument | null
  label: ExtractionAnnotationLabel | null
  validation_status: AIAnnotationValidationStatus
  validation_issues: AIAnnotationIssue[]
  elapsed_ms: number
  fragment_count?: number
  truncated_fragments?: string[]
  fragment_warnings?: string[]
  token_usage: AIAnnotationTokenUsage
}

export type AIAnnotationJobStatus = 'queued' | 'running' | 'success' | 'failed'
export type AIAnnotationJobOperation = 'generate' | 'repair'

export interface AIAnnotationJob {
  id: number
  task_id: number
  passage_id: number
  passage_title: string
  operation: AIAnnotationJobOperation
  status: AIAnnotationJobStatus
  provider: string
  model: string
  error_message: string
  client_started_at: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  submitted_at: string | null
  token_usage: AIAnnotationTokenUsage
  first_round_validation_status: string
  first_round_error_count: number
  first_round_warning_count: number
  first_round_truncated_count: number
  final_difference_count: number
  saved_result_source_job_id: number | null
  result: AIAnnotationResult | null
  saved_result: AIAnnotationResult | null
}

export async function generateAIAnnotation(
  taskId: number,
  config: AIAnnotationConfig,
  promptOverrides?: AIAnnotationPromptOverrides,
  clientStartedAt = new Date().toISOString(),
) {
  const payload: {
    task_id: number
    config: AIAnnotationConfig
    prompt_overrides?: AIAnnotationPromptOverrides
    client_started_at: string
  } = {
    task_id: taskId,
    config,
    client_started_at: clientStartedAt,
  }
  if (promptOverrides) payload.prompt_overrides = promptOverrides
  const { data } = await apiClient.post<AIAnnotationJob>(
    '/annotations/extraction/ai/jobs',
    payload,
  )
  return data
}

export const enqueueAIAnnotation = generateAIAnnotation

export async function listAIAnnotationJobs() {
  const { data } = await apiClient.get<AIAnnotationJob[]>('/annotations/extraction/ai/jobs')
  return data
}

export async function fetchAIAnnotationJob(jobId: number) {
  const { data } = await apiClient.get<AIAnnotationJob>(
    `/annotations/extraction/ai/jobs/${jobId}`,
  )
  return data
}

export async function fetchAIAnnotationTask(taskId: number) {
  const { data } = await apiClient.get<AIAnnotationTaskContext>(
    `/annotations/extraction/ai/tasks/${taskId}`,
  )
  return data
}

export async function previewAIAnnotationPrompt(taskId: number, config: AIAnnotationConfig) {
  const { data } = await apiClient.post<AIAnnotationPrompt>('/annotations/extraction/ai/prompt', {
    task_id: taskId,
    config,
  })
  return data
}

export async function validateAIAnnotation(
  taskId: number,
  content: string,
  jobId?: number,
) {
  const { data } = await apiClient.post<AIAnnotationResult>('/annotations/extraction/ai/validate', {
    task_id: taskId,
    ...(jobId ? { job_id: jobId } : {}),
    content,
  })
  return data
}

export async function enqueueAIAnnotationRepair(
  taskId: number,
  content: string,
  config: AIAnnotationConfig,
  jobId?: number,
  clientStartedAt = new Date().toISOString(),
) {
  const { data } = await apiClient.post<AIAnnotationJob>('/annotations/extraction/ai/repair', {
    task_id: taskId,
    ...(jobId ? { job_id: jobId } : {}),
    content,
    config,
    client_started_at: clientStartedAt,
  })
  return data
}

export const repairAIAnnotation = enqueueAIAnnotationRepair

export interface AIAnnotationMetricError {
  code: string
  example_message: string
  count: number
  session_count: number
}

export interface AIAnnotationMetricDifference {
  category: string
  label: string
  count: number
}

export interface AIAnnotationDailyMetric {
  date: string
  session_count: number
  submitted_count: number
  total_tokens: number
  first_round_error_count: number
}

export interface AIAnnotationMetricsSummary {
  session_count: number
  completed_first_round_count: number
  failed_first_round_count: number
  first_pass_valid_count: number
  first_pass_valid_rate: number
  submitted_count: number
  submission_rate: number
  average_first_round_ms: number
  average_end_to_end_ms: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  total_cache_hit_tokens: number
  total_cache_miss_tokens: number
  total_llm_call_count: number
  total_repair_rounds: number
  total_first_round_errors: number
  total_final_differences: number
}

export interface AIAnnotationMetricsRecord {
  session_id: number
  task_id: number
  passage_id: number
  passage_title: string
  requested_by: number
  requested_by_email: string
  provider: string
  model: string
  status: string
  first_round_validation_status: string
  client_started_at: string
  queued_at: string
  started_at: string | null
  first_round_finished_at: string | null
  submitted_at: string | null
  queue_wait_ms: number | null
  first_round_elapsed_ms: number | null
  end_to_end_ms: number | null
  token_usage: AIAnnotationTokenUsage
  repair_rounds: number
  first_round_errors: AIAnnotationIssue[]
  first_round_warning_count: number
  first_round_truncated_count: number
  final_difference_count: number
  final_difference_categories: Record<string, number>
  submission_id: number | null
}

export interface AIAnnotationMetrics {
  generated_at: string
  days: number
  summary: AIAnnotationMetricsSummary
  validation_errors: AIAnnotationMetricError[]
  final_differences: AIAnnotationMetricDifference[]
  daily: AIAnnotationDailyMetric[]
  records: AIAnnotationMetricsRecord[]
}

export async function fetchAIAnnotationMetrics(days = 30, limit = 500) {
  const { data } = await apiClient.get<AIAnnotationMetrics>(
    '/annotations/extraction/ai/metrics',
    { params: { days, limit } },
  )
  return data
}

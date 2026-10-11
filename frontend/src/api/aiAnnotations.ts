import apiClient from './client'
import type {
  CheckState,
  EventLocation,
  EventTime,
  EventType,
  ExtractionAnnotationLabel,
  FactState,
  RelationCode,
} from './extractionAnnotations'

export interface AIAnnotationConfig {
  provider: string
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
  generation_job_id: number | null
  review_status: 'not_started' | 'draft' | 'submitted'
  review_accuracy: number | null
  review_submitted_at: string | null
  result: AIAnnotationResult | null
  saved_result: AIAnnotationResult | null
  reused_existing?: boolean
  archived_at?: string | null
  lineage_job_id?: number | null
  active_generation_count?: number
  generation_count?: number
  generation_limit?: number
  active_job_id?: number | null
  active_operation?: AIAnnotationJobOperation | null
  active_status?: 'queued' | 'running' | null
}

export interface AIAnnotationGenerationSummary {
  job_id: number
  task_id: number
  passage_id: number
  provider: string
  model: string
  temperature: number | null
  prompt_overridden: boolean
  status: AIAnnotationJobStatus
  validation_status: string
  created_at: string
  finished_at: string | null
  review_status: 'not_started' | 'draft' | 'submitted'
  review_accuracy: number | null
  review_submitted_at: string | null
  has_working_copy: boolean
  latest_job_id: number
  latest_operation: AIAnnotationJobOperation
  latest_status: AIAnnotationJobStatus
  latest_provider: string
  latest_model: string
  archived_at: string | null
  repair_count: number
}

export interface AIAnnotationGenerationGroup {
  provider: string
  model: string
  generations: AIAnnotationGenerationSummary[]
}

export interface AIAnnotationGenerationList {
  task_id: number
  active_job_id: number | null
  active_operation: AIAnnotationJobOperation | null
  active_status: 'queued' | 'running' | null
  active_generation_count: number
  generation_count: number
  generation_limit: number
  groups: AIAnnotationGenerationGroup[]
}

export type AIAnnotationReviewVerdict = 'unreviewed' | 'correct' | 'incorrect'

export interface AIAnnotationReviewStandard {
  id: string
  group: string
  title: string
  instruction: string
}

export interface AIAnnotationReviewItem {
  id: string
  category: string
  path: string
  title: string
  question: string
  detail: string
  field_label: string
  ai_value: string
  group_key: string
  group_title: string
  section_key: string
  section_title: string
  value_kind: 'text' | 'evidence' | 'coverage' | string
  correction_hint: string
  standard_ids: string[]
  evidence: import('./extractionAnnotations').EvidenceSpan[]
  verdict: AIAnnotationReviewVerdict
  reason: string
  correction: string
  correction_evidence: import('./extractionAnnotations').EvidenceSpan[]
  correction_data: AIAnnotationReviewAdditionData
  remove_item: boolean
}

export interface AIAnnotationReviewOmission {
  id: string
  category: string
  target_path: string
  description: string
  correction: string
  evidence: import('./extractionAnnotations').EvidenceSpan[]
  evidence_note: string
  addition_type: AIAnnotationReviewAdditionType
  parent_key: string
  data: AIAnnotationReviewAdditionData
}

export type AIAnnotationReviewAdditionType =
  | 'note'
  | 'person'
  | 'life_event'
  | 'historical_event'
  | 'person_relation'
  | 'evidence'
  | 'excluded_mention'
  | 'unresolved_item'
  | 'schema_conflict'

export interface AIAnnotationReviewAdditionData {
  key?: string
  name_surface?: string
  completed_name?: string
  completion_reason?: string
  courtesy_name?: string
  hao?: string
  titles?: string[]
  level?: 1 | 2 | 3
  level_reason?: string
  event_checks?: Record<EventType, CheckState>
  event_type?: EventType
  state?: FactState
  time?: EventTime
  location?: EventLocation
  official_title?: string
  event_name?: string
  outside_dictionary?: boolean
  relation_summary?: string
  relation_category?: string
  source_person_key?: string
  target_person_key?: string
  codes?: RelationCode[]
  reverse_codes?: RelationCode[]
  reverse_note?: string
  reason?: string
  category?: string
  code?: string
  note?: string
  target_type?: string
  target_key?: string
}

export interface AIAnnotationReviewSummary {
  total_item_count: number
  reviewed_item_count: number
  correct_count: number
  incorrect_count: number
  unreviewed_count: number
  omission_count: number
  accuracy: number
}

export interface AIAnnotationReview {
  id: number | null
  generation_job_id: number
  task_id: number
  passage_id: number
  passage_title: string
  passage_context: string
  status: 'draft' | 'submitted'
  standard_version: string
  standards: AIAnnotationReviewStandard[]
  items: AIAnnotationReviewItem[]
  omissions: AIAnnotationReviewOmission[]
  summary: AIAnnotationReviewSummary
  ground_truth_document: AIAnnotationExportDocument | null
  reviewer_id: number
  editable: boolean
  blind_submitted_at: string | null
  started_at: string | null
  created_at: string | null
  updated_at: string | null
  submitted_at: string | null
}

export interface AIAnnotationReviewUpdate {
  decisions: Array<Pick<AIAnnotationReviewItem, 'id' | 'verdict' | 'reason' | 'correction' | 'correction_evidence' | 'correction_data' | 'remove_item'>>
  omissions: AIAnnotationReviewOmission[]
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

export async function listAIAnnotationGenerations(taskId: number, includeArchived = false) {
  const { data } = await apiClient.get<AIAnnotationGenerationList>(
    `/annotations/extraction/ai/tasks/${taskId}/generations`,
    { params: { include_archived: includeArchived } },
  )
  return data
}

export async function archiveAIAnnotationGeneration(jobId: number) {
  const { data } = await apiClient.post<AIAnnotationGenerationSummary>(
    `/annotations/extraction/ai/jobs/${jobId}/archive`,
  )
  return data
}

export async function fetchAIAnnotationReview(jobId: number) {
  const { data } = await apiClient.get<AIAnnotationReview>(
    `/annotations/extraction/ai/jobs/${jobId}/review`,
  )
  return data
}

export async function startAIAnnotationReview(jobId: number) {
  const { data } = await apiClient.post<AIAnnotationReview>(
    `/annotations/extraction/ai/jobs/${jobId}/review/start`,
  )
  return data
}

function reviewPayload(review: AIAnnotationReview): AIAnnotationReviewUpdate {
  return {
    decisions: review.items.map((item) => ({
      id: item.id,
      verdict: item.verdict,
      reason: item.reason,
      correction: item.correction,
      correction_evidence: item.correction_evidence ?? [],
      correction_data: item.correction_data ?? {},
      remove_item: item.remove_item,
    })),
    omissions: review.omissions,
  }
}

export async function saveAIAnnotationReview(jobId: number, review: AIAnnotationReview) {
  const payload = reviewPayload(review)
  const { data } = await apiClient.put<AIAnnotationReview>(
    `/annotations/extraction/ai/jobs/${jobId}/review`,
    {
      decisions: payload.decisions.map(({ id, verdict, reason, correction, correction_evidence, correction_data, remove_item }) => ({
        item_id: id,
        verdict,
        reason,
        correction,
        correction_evidence,
        correction_data,
        remove_item,
      })),
      omissions: payload.omissions,
    },
  )
  return data
}

export async function submitAIAnnotationReview(jobId: number, review: AIAnnotationReview) {
  const payload = reviewPayload(review)
  const { data } = await apiClient.post<AIAnnotationReview>(
    `/annotations/extraction/ai/jobs/${jobId}/review/submit`,
    {
      decisions: payload.decisions.map(({ id, verdict, reason, correction, correction_evidence, correction_data, remove_item }) => ({
        item_id: id,
        verdict,
        reason,
        correction,
        correction_evidence,
        correction_data,
        remove_item,
      })),
      omissions: payload.omissions,
    },
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

export interface AIAnnotationMetricHumanReviewErrorType {
  category: string
  field_label: string
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
  human_processing_count: number
  average_human_processing_ms: number
  average_human_review_ms: number
  post_import_edit_count: number
  average_post_import_edit_ms: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  total_cache_hit_tokens: number
  total_cache_miss_tokens: number
  total_llm_call_count: number
  total_repair_rounds: number
  total_first_round_errors: number
  total_final_differences: number
  human_reviewed_count: number
  human_scored_count: number
  average_human_accuracy: number
  total_human_correct: number
  total_human_incorrect: number
  total_human_omissions: number
  human_omission_categories: Record<string, number>
}

export interface AIAnnotationMetricOmission {
  category: string
  target_path: string
  description: string
  correction: string
  count: number
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
  turnaround_elapsed_ms: number | null
  human_processing_started_at: string | null
  human_processing_elapsed_ms: number | null
  human_review_started_at: string | null
  human_review_elapsed_ms: number | null
  ai_imported_at: string | null
  post_import_edit_elapsed_ms: number | null
  token_usage: AIAnnotationTokenUsage
  repair_rounds: number
  first_round_errors: AIAnnotationIssue[]
  first_round_warning_count: number
  first_round_truncated_count: number
  final_difference_count: number
  final_difference_categories: Record<string, number>
  human_review_status: string
  human_review_accuracy: number | null
  human_review_correct_count: number
  human_review_incorrect_count: number
  human_review_omission_count: number
  human_review_error_categories: Record<string, number>
  human_review_error_types: AIAnnotationMetricHumanReviewErrorType[]
  human_review_omission_categories: Record<string, number>
  human_review_omissions: AIAnnotationMetricOmission[]
  human_review_submitted_at: string | null
  submission_id: number | null
}

export interface AIAnnotationProviderModelMetrics {
  provider: string
  model: string
  session_count: number
  completed_first_round_count: number
  failed_first_round_count: number
  human_reviewed_count: number
  average_human_accuracy: number
  total_tokens: number
  first_pass_valid_count: number
}

export interface AIAnnotationMetrics {
  generated_at: string
  days: number
  summary: AIAnnotationMetricsSummary
  validation_errors: AIAnnotationMetricError[]
  final_differences: AIAnnotationMetricDifference[]
  daily: AIAnnotationDailyMetric[]
  records: AIAnnotationMetricsRecord[]
  by_provider_model?: AIAnnotationProviderModelMetrics[]
}

export async function fetchAIAnnotationMetrics(days = 30, limit = 500) {
  const { data } = await apiClient.get<AIAnnotationMetrics>(
    '/annotations/extraction/ai/metrics',
    { params: { days, limit } },
  )
  return data
}

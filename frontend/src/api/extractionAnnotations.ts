import apiClient from './client'

export const EVENT_TYPES = ['出生', '籍贯', '死亡', '埋葬', '任职'] as const
export const RELATION_CODES = ['F', 'M', 'S', 'D', 'H', 'W', 'Z', 'C', 'B', 'O'] as const

export type EventType = (typeof EVENT_TYPES)[number]
export type RelationCode = (typeof RELATION_CODES)[number]
export type CheckState = 'unreviewed' | 'has_fact' | 'not_mentioned' | 'uncertain' | 'unsupported'
export type FactState = 'confirmed' | 'uncertain' | 'unsupported'
export type FieldState = 'present' | 'not_mentioned' | 'not_applicable' | 'uncertain' | 'unsupported'

export interface EvidenceSpan {
  id: string
  source: 'context'
  quote: string
  start: number
  end: number
  start_utf16: number
  end_utf16: number
}

export interface PassageAnnotation {
  source_type: 'epitaph' | 'history' | 'uncertain'
  material_status: 'complete' | 'issue' | 'uncertain'
  is_female: boolean | null
  is_damaged: boolean | null
  is_clergy: boolean | null
  has_courtesy_name: boolean | null
  era: string
  era_basis: string
  note: string
}

export interface EventTime {
  state: FieldState
  raw: string
  era: string
  era_year: number | null
  gregorian_year: number | null
  month_text: string
  day_text: string
}

export interface EventLocation {
  state: FieldState
  raw: string
  dao: string
  fu: string
  zhou: string
  jun: string
  xian: string
  other: string
}

export interface LifeEventAnnotation {
  key: string
  event_type: EventType
  state: FactState
  time: EventTime
  location: EventLocation
  official_title: string
  evidence: EvidenceSpan[]
  note: string
}

export interface HistoricalEventAnnotation {
  key: string
  event_name: string
  outside_dictionary: boolean
  relation_summary: string
  state: FactState
  evidence: EvidenceSpan[]
  note: string
}

export interface PersonAnnotation {
  key: string
  name_surface: string
  completed_name: string
  completion_reason: string
  courtesy_name: string
  hao: string
  titles: string[]
  level: 1 | 2 | 3
  level_reason: string
  mentions: EvidenceSpan[]
  event_checks: Record<EventType, CheckState>
  life_events: LifeEventAnnotation[]
  historical_events: HistoricalEventAnnotation[]
}

export interface PersonRelationAnnotation {
  key: string
  source_person_key: string
  target_person_key: string
  codes: RelationCode[]
  reverse_codes: RelationCode[]
  note: string
  reverse_note: string
  state: FactState
  evidence: EvidenceSpan[]
}

export interface ExcludedMention {
  key: string
  reason: string
  evidence: EvidenceSpan[]
}

export interface UnresolvedItem {
  key: string
  category: string
  note: string
  evidence: EvidenceSpan[]
}

export interface SchemaConflict {
  key: string
  code: string
  note: string
  evidence: EvidenceSpan[]
}

export interface ExtractionAnnotationLabel {
  schema_version: string
  passage: PassageAnnotation
  persons: PersonAnnotation[]
  person_relations: PersonRelationAnnotation[]
  excluded_mentions: ExcludedMention[]
  unresolved_items: UnresolvedItem[]
  schema_conflicts: SchemaConflict[]
}

export interface ExtractionTaskSummary {
  id: number
  passage_id: number
  passage_title: string
  context_sha256: string
  spec_version: string
  status: string
  priority: number
  required_annotation_count: number
  claimed_count: number
  submitted_count: number
  available_slots: number
  submission_id: number | null
  submission_state: string | null
  slot_no: number | null
  revision: number | null
  updated_at: string
  assignments: ExtractionTaskAssignment[]
}

export interface ExtractionTaskAssignment {
  submission_id: number
  slot_no: number
  annotator_id: number
  annotator_email: string
  state: string
  submitted_at: string | null
  updated_at: string
}

export interface AnnotationPassage {
  doc_id: number
  title: string
  context: string
  source_type: string
  workflow_status: string
}

export interface ExtractionSubmission {
  id: number
  task_id: number
  slot_no: number
  state: string
  revision: number
  label: ExtractionAnnotationLabel
  submitted_at: string | null
  updated_at: string
}

export interface ExtractionTaskDetail {
  id: number
  context_sha256: string
  spec_version: string
  status: string
  required_annotation_count: number
  passage: AnnotationPassage
  submission: ExtractionSubmission
}

export interface AnnotationEraEntry {
  era: string
  dynasty: string
  start_year: number
  end_year: number
  simplified: string
  traditional: string
}

export interface AnnotationHistoricalEventEntry {
  id: number
  year_label: string
  event_name: string
  event_details: string
  start_year: number | null
  end_year: number | null
}

export interface AnnotationRelationCodeEntry {
  code: RelationCode
  meaning: string
  direction_hint: string
}

export interface AnnotationDictionaries {
  eras: AnnotationEraEntry[]
  historical_events: AnnotationHistoricalEventEntry[]
  relation_codes: AnnotationRelationCodeEntry[]
}

export type AdjudicationDecision = 'a' | 'b' | 'manual'
export type AdjudicationOperation = 'replace' | 'add' | 'remove'

export interface AdjudicationDifference {
  id: string
  category: string
  label: string
  path: string
  operation: AdjudicationOperation
  value_a: unknown
  value_b: unknown
}

export interface AdjudicationResolution {
  difference_id: string
  decision: AdjudicationDecision
  manual_value: unknown
  note: string
}

export interface AdjudicationSubmission {
  id: number
  slot_no: number
  revision: number
  submitted_at: string
  label: ExtractionAnnotationLabel
}

export interface ExtractionAdjudicationDraft {
  id: number | null
  revision: number
  resolutions: AdjudicationResolution[]
  gold_label: ExtractionAnnotationLabel
  change_reason: string
  updated_at: string | null
}

export interface ExtractionGoldVersion {
  id: number
  task_id: number
  version: number
  label: ExtractionAnnotationLabel
  source_submission_ids: number[]
  reviewer_id: number
  change_reason: string
  locked_at: string
}

export interface ExtractionAdjudicationSummary {
  task_id: number
  passage_id: number
  passage_title: string
  task_status: string
  submitted_count: number
  difference_count: number
  resolved_count: number
  latest_gold_version: number | null
  updated_at: string
}

export interface ExtractionAdjudicationDetail {
  task_id: number
  task_status: string
  spec_version: string
  context_sha256: string
  passage: AnnotationPassage
  submissions: AdjudicationSubmission[]
  differences: AdjudicationDifference[]
  draft: ExtractionAdjudicationDraft
  latest_gold: ExtractionGoldVersion | null
}

export interface ExtractionAdjudicationUpdate {
  revision: number
  resolutions: AdjudicationResolution[]
  label_override?: ExtractionAnnotationLabel | null
  change_reason?: string
}

export async function listExtractionTasks() {
  const { data } = await apiClient.get<ExtractionTaskSummary[]>('/annotations/extraction/tasks')
  return data
}

export async function createExtractionTask(payload: {
  passage_id: number
  required_annotation_count?: number
  priority?: number
  spec_version?: string
}) {
  const { data } = await apiClient.post<ExtractionTaskSummary>('/annotations/extraction/tasks', payload)
  return data
}

export async function claimExtractionTask(taskId: number) {
  const { data } = await apiClient.post<ExtractionTaskDetail>(
    `/annotations/extraction/tasks/${taskId}/claim`,
  )
  return data
}

export async function releaseExtractionDraft(taskId: number, submissionId: number) {
  const { data } = await apiClient.delete<ExtractionTaskSummary>(
    `/annotations/extraction/tasks/${taskId}/submissions/${submissionId}`,
  )
  return data
}

export async function resetExtractionTask(taskId: number) {
  const { data } = await apiClient.post<ExtractionTaskSummary>(
    `/annotations/extraction/tasks/${taskId}/reset`,
  )
  return data
}

export async function deleteExtractionTask(taskId: number) {
  await apiClient.delete(`/annotations/extraction/tasks/${taskId}`)
}

export async function fetchExtractionTask(taskId: number) {
  const { data } = await apiClient.get<ExtractionTaskDetail>(
    `/annotations/extraction/tasks/${taskId}`,
  )
  return data
}

export async function saveExtractionDraft(
  taskId: number,
  revision: number,
  label: ExtractionAnnotationLabel,
) {
  const { data } = await apiClient.put<ExtractionTaskDetail>(
    `/annotations/extraction/tasks/${taskId}/draft`,
    { revision, label },
  )
  return data
}

export async function importExtractionDraft(
  taskId: number,
  revision: number,
  file: File,
  allowValidationIssues = false,
  aiJobId?: number,
) {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await apiClient.post<ExtractionTaskDetail>(
    `/annotations/extraction/tasks/${taskId}/import`,
    formData,
    {
      params: {
        revision,
        allow_validation_issues: allowValidationIssues,
        ...(aiJobId ? { ai_job_id: aiJobId } : {}),
      },
    },
  )
  return data
}

export async function submitExtractionAnnotation(
  taskId: number,
  revision: number,
  label: ExtractionAnnotationLabel,
) {
  const { data } = await apiClient.post<ExtractionTaskDetail>(
    `/annotations/extraction/tasks/${taskId}/submit`,
    { revision, label },
  )
  return data
}

export async function fetchAnnotationDictionaries(query = '') {
  const { data } = await apiClient.get<AnnotationDictionaries>(
    '/annotations/extraction/dictionaries',
    { params: { query, limit: 1000 } },
  )
  return data
}

export async function listExtractionAdjudications() {
  const { data } = await apiClient.get<ExtractionAdjudicationSummary[]>(
    '/annotations/extraction/adjudications',
  )
  return data
}

export async function fetchExtractionAdjudication(taskId: number) {
  const { data } = await apiClient.get<ExtractionAdjudicationDetail>(
    `/annotations/extraction/adjudications/${taskId}`,
  )
  return data
}

export async function saveExtractionAdjudicationDraft(
  taskId: number,
  payload: ExtractionAdjudicationUpdate,
) {
  const { data } = await apiClient.put<ExtractionAdjudicationDetail>(
    `/annotations/extraction/adjudications/${taskId}/draft`,
    payload,
  )
  return data
}

export async function lockExtractionGold(
  taskId: number,
  payload: ExtractionAdjudicationUpdate,
) {
  const { data } = await apiClient.post<ExtractionAdjudicationDetail>(
    `/annotations/extraction/adjudications/${taskId}/lock`,
    payload,
  )
  return data
}

export async function downloadExtractionGold(taskId: number, version?: number) {
  const response = await apiClient.get<Blob>(
    `/annotations/extraction/adjudications/${taskId}/export`,
    { params: version ? { version } : undefined, responseType: 'blob' },
  )
  const disposition = String(response.headers['content-disposition'] ?? '')
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1]
    ?? `extraction-gold-task-${taskId}.yaml`
  return { blob: response.data, filename }
}

export async function fetchExtractionGold(taskId: number, version?: number) {
  const { data } = await apiClient.get<ExtractionGoldVersion>(
    `/annotations/extraction/adjudications/${taskId}/gold`,
    { params: version ? { version } : undefined },
  )
  return data
}

export async function importExtractionGold(
  taskId: number,
  file: File,
  changeReason = '',
) {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await apiClient.post<ExtractionGoldVersion>(
    `/annotations/extraction/adjudications/${taskId}/import`,
    formData,
    { params: changeReason ? { change_reason: changeReason } : undefined },
  )
  return data
}
